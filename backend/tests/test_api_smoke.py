
from fastapi.testclient import TestClient


def _fresh_client() -> TestClient:
    from app.main import app  # importa después de fijar DATABASE_URL en conftest

    return TestClient(app)


def _login(
    client: TestClient, operator_name: str = "GLAGO", position: str = "FMP SUR", pin: str = "1234"
) -> dict:
    """Abre turno y deja el token de sesión seteado en el cliente, para que
    las siguientes escrituras (POST/PATCH/DELETE) queden autorizadas. El
    nombre debe existir en la nómina sembrada (seed_data.CONTROLLER_NAMES)
    con ese PIN -- ver seed_data.DEFAULT_PIN."""
    resp = client.post("/shifts/clock-in", json={"operator_name": operator_name, "position": position, "pin": pin})
    assert resp.status_code == 200, resp.text
    shift = resp.json()
    client.headers["X-Shift-Token"] = shift["session_token"]
    return shift


def test_health():
    client = _fresh_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_mutating_endpoints_reject_missing_or_invalid_token():
    client = _fresh_client()
    payload = {"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}

    # sin cabecera X-Shift-Token
    assert client.post("/flights", json=payload).status_code in (401, 422)

    # con un token que no corresponde a ningún turno abierto
    client.headers["X-Shift-Token"] = "no-existe"
    assert client.post("/flights", json=payload).status_code == 401

    # con token válido, funciona
    _login(client)
    resp = client.post("/flights", json=payload)
    assert resp.status_code == 200
    flight_id = resp.json()["id"]

    # cerrar turno invalida el token para escrituras futuras
    shift = client.get("/shifts/active").json()[0]
    client.post(f"/shifts/{shift['id']}/clock-out")
    resp = client.patch(f"/flights/{flight_id}", json={"observaciones": "x"})
    assert resp.status_code == 401


def test_operator_name_is_derived_from_shift_not_from_payload():
    """El nombre que firma un cambio sale del turno autenticado -- un cliente
    no puede hacerse pasar por otro operador mandando `updated_by` distinto."""
    client = _fresh_client()
    _login(client, operator_name="GLAGO")
    resp = client.post("/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"})
    flight_id = resp.json()["id"]

    resp = client.patch(
        f"/flights/{flight_id}",
        json={"observaciones": "nota", "updated_by": "ALGUIEN MAS"},
    )
    assert resp.json()["updated_by"] == "GLAGO"


def test_flight_crud_and_ctot_cascade():
    client = _fresh_client()
    _login(client)

    payload = {
        "sector": "SUR",
        "flight_date": "2026-06-03",
        "vuelo": "LPE2026",
        "hora": "2248",
        "eta_aircon": "0019",
        "etd1": "2311",
        "ctot1": "2319",
    }
    resp = client.post("/flights", json=payload)
    assert resp.status_code == 200, resp.text
    flight = resp.json()
    assert flight["numero_fila"] == 1
    assert flight["dla_minutos"] == 8

    resp = client.get("/flights", params={"sector": "SUR", "flight_date": "2026-06-03"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.patch(f"/flights/{flight['id']}", json={"observaciones": "revisado"})
    assert resp.status_code == 200
    assert resp.json()["observaciones"] == "revisado"


def test_shift_clock_in_out():
    client = _fresh_client()
    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "1234"},
    )
    assert resp.status_code == 200
    shift = resp.json()
    assert shift["end_time"] is None
    assert shift["session_token"]

    resp = client.get("/shifts/active")
    assert len(resp.json()) == 1

    client.headers["X-Shift-Token"] = shift["session_token"]
    resp = client.post(f"/shifts/{shift['id']}/clock-out")
    assert resp.status_code == 200
    closed = resp.json()
    assert closed["end_time"] is not None
    assert closed["duration_minutes"] >= 0


def test_shift_blocks_second_position_but_allows_resume():
    client = _fresh_client()
    resp = client.post("/shifts/clock-in", json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "1234"})
    assert resp.status_code == 200

    # misma persona, misma posición -> se reengancha al turno abierto
    resp = client.post("/shifts/clock-in", json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "1234"})
    assert resp.status_code == 200
    client.headers["X-Shift-Token"] = resp.json()["session_token"]
    assert resp.json()["id"] == client.get("/shifts/active").json()[0]["id"]

    # misma persona, otra posición -> bloqueado
    resp = client.post("/shifts/clock-in", json={"operator_name": "GLAGO", "position": "FMP NOR", "pin": "1234"})
    assert resp.status_code == 409


def test_dgac_read_only_profile_cannot_write_but_can_clock_out():
    """El perfil DGAC (seed_data.DGAC_VIEWER_NAME) puede iniciar y cerrar
    turno normalmente, y ver datos, pero ningún endpoint de escritura acepta
    su token -- sin importar qué controles muestre el frontend."""
    client = _fresh_client()
    shift = _login(client, operator_name="DGAC1", pin="2580")
    assert shift["read_only"] is True

    resp = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    )
    assert resp.status_code == 403

    # las lecturas siguen andando igual (no requieren turno para nada)
    resp = client.get("/flights", params={"sector": "SUR", "flight_date": "2026-06-03"})
    assert resp.status_code == 200

    # puede cerrar su propio turno sin problema
    resp = client.post(f"/shifts/{shift['id']}/clock-out")
    assert resp.status_code == 200


def test_clock_in_rejects_wrong_pin_or_unknown_name():
    client = _fresh_client()
    resp = client.post("/shifts/clock-in", json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "0000"})
    assert resp.status_code == 401

    resp = client.post(
        "/shifts/clock-in", json={"operator_name": "NO EXISTE", "position": "FMP SUR", "pin": "1234"}
    )
    assert resp.status_code == 401

    # no abrió ningún turno con los intentos fallidos: el único activo es el
    # que abre esta consulta (las lecturas ahora exigen turno)
    ok = client.post(
        "/shifts/clock-in", json={"operator_name": "OCRUZ", "position": "FMP NOR", "pin": "1234"}
    ).json()
    client.headers["X-Shift-Token"] = ok["session_token"]
    activos = client.get("/shifts/active").json()
    assert [s["operator_name"] for s in activos] == ["OCRUZ"]


def test_reattach_ignora_mayusculas_y_espacios_en_el_nombre():
    """Regresión: "OCRUZ", "OCRUZ" y "  Ocruz  " deben reenganchar al mismo
    turno en vez de abrir uno nuevo por cada variante de tipeo."""
    client = _fresh_client()
    first = client.post(
        "/shifts/clock-in", json={"operator_name": "OCRUZ", "position": "FMP SUR", "pin": "1234"}
    ).json()

    resp = client.post("/shifts/clock-in", json={"operator_name": "OCRUZ", "position": "FMP SUR", "pin": "1234"})
    assert resp.status_code == 200
    assert resp.json()["id"] == first["id"]

    resp = client.post(
        "/shifts/clock-in", json={"operator_name": "  Ocruz  ", "position": "FMP SUR", "pin": "1234"}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == first["id"]

    client.headers["X-Shift-Token"] = first["session_token"]
    assert len(client.get("/shifts/active").json()) == 1


def test_reattach_generates_token_for_shift_opened_before_auth_existed():
    """Regresión: un turno abierto antes de que existiera session_token (dato
    viejo, columna NULL) rompía con 500 al reenganchar, porque ShiftAuthOut
    exige un token no nulo. Debe generarse uno nuevo en vez de fallar."""
    from datetime import datetime

    from app import database
    from app.models import Position, ShiftLog

    db = database.SessionLocal()
    try:
        old_shift = ShiftLog(
            operator_name="MSALAZARV",
            position=Position.FMP_SUR,
            start_time=datetime(2026, 7, 1, 8, 0),
            session_token=None,
        )
        db.add(old_shift)
        db.commit()
        db.refresh(old_shift)
        shift_id = old_shift.id
    finally:
        db.close()

    client = _fresh_client()
    resp = client.post(
        "/shifts/clock-in", json={"operator_name": "MSALAZARV", "position": "FMP SUR", "pin": "1234"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == shift_id
    assert body["session_token"]


def test_itinerary_upload_csv():
    client = _fresh_client()
    _login(client)
    csv_content = (
        "call_sign,direction,hora_utc,aerodromo,tipo_aeronave,tipo_servicio\n"
        "LPE2026,ARR,2200,SPZO,A320,J\n"
    )
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("itin.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["filas_aceptadas"] == 1
    assert report["filas_rechazadas"] == 0


def test_forecast_endpoint():
    client = _fresh_client()
    _login(client)
    client.post(
        "/flights",
        json={
            "sector": "SUR",
            "flight_date": "2026-06-03",
            "vuelo": "LPE2026",
            "eta_aircon": "0019",
        },
    )
    resp = client.get("/forecast", params={"flight_date": "2026-06-03"})
    assert resp.status_code == 200
    body = resp.json()
    buckets = body["buckets"]
    assert len(buckets) == 24
    assert body["sector"] is None
    assert sum(b["trabajado"] for b in buckets) == 1
    assert all(b["capacidad_maxima"] == 49 for b in buckets)


def test_forecast_sector_filter_and_pronosticado():
    client = _fresh_client()
    _login(client)
    csv_content = (
        "call_sign,direction,hora_utc,aerodromo,tipo_aeronave,tipo_servicio\n"
        "LPE2026,ARR,0019,SPZO,A320,J\n"
        "LPE9999,DEP,0019,SPZO,A320,J\n"
    )
    client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("itin.csv", csv_content, "text/csv")},
    )
    client.post(
        "/flights",
        json={"sector": "NOR", "flight_date": "2026-06-03", "vuelo": "LPE2026", "eta_aircon": "0019"},
    )

    resp = client.get("/forecast", params={"flight_date": "2026-06-03", "sector": "SUR"})
    body = resp.json()
    assert sum(b["trabajado"] for b in body["buckets"]) == 0  # el vuelo está en NOR, no en SUR
    assert sum(b["pronosticado"] for b in body["buckets"]) == 2  # pronosticado no filtra por sector


def test_forecast_cuenta_llegadas_desde_cusco():
    """La serie que usan las posiciones FMP CUSCO: arribos con origen Cusco.

    Cubre el caso que se pierde facil -- en la base conviven "SPZO" (carga de
    temporada, que convierte IATA->OACI) y "CUZ" sin convertir (formato
    antiguo, que copia la celda ORIGEN tal cual): las dos son Cusco.
    """
    client = _fresh_client()
    _login(client)
    # Las dos primeras cuentan (Cusco por OACI y por IATA). No cuentan: el
    # despegue (va hacia Cusco, no viene), Arequipa (nacional, otro origen) y
    # Santiago (ni nacional ni Cusco).
    csv_content = """call_sign,direction,hora_utc,aerodromo,tipo_aeronave,tipo_servicio
LPE2100,ARR,1300,SPZO,A320,J
LPE2101,ARR,1330,CUZ,A320,J
LPE2102,DEP,1345,SPZO,A320,J
LPE2103,ARR,1400,SPQU,A320,J
LPE2104,ARR,1430,SCEL,A320,J
"""
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-04", "confirmado": True},
        files={"file": ("itin.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200

    buckets = client.get("/forecast", params={"flight_date": "2026-06-04"}).json()["buckets"]
    por_hora = {b["rango_hora"]: b for b in buckets}

    franja_13 = por_hora["13:00 - 13:59"]
    assert franja_13["pronosticado_llegadas_cusco"] == 2  # SPZO y CUZ
    # El complemento exacto que apila el gráfico: nacionales que no son Cusco.
    # Acá es 0 porque las dos nacionales de la franja son justamente las de
    # Cusco -- y confirma que no se solapan con la serie de Cusco.
    assert franja_13["pronosticado_llegadas_nacionales_otras"] == 0
    # Las llegadas nacionales solo ven una: "CUZ" es Cusco pero no empieza con
    # el prefijo OACI "SP". Por eso Cusco se cuenta aparte y no anidado ahí.
    assert franja_13["pronosticado_llegadas_nacionales"] == 1
    assert franja_13["pronosticado"] == 3  # + el despegue hacia Cusco
    assert franja_13["pronosticado_despegues"] == 1  # ese mismo despegue

    franja_14 = por_hora["14:00 - 14:59"]
    assert franja_14["pronosticado_llegadas_cusco"] == 0
    assert franja_14["pronosticado_llegadas_nacionales"] == 1  # SPQU; SCEL no es nacional
    assert franja_14["pronosticado_llegadas_nacionales_otras"] == 1  # el mismo SPQU
    assert franja_14["pronosticado"] == 2
    assert franja_14["pronosticado_despegues"] == 0  # la franja 14 es solo de arribos

    # Las series son subconjuntos del total del aeródromo, y las dos partes que
    # el gráfico apila (Cusco + otras nacionales) nunca se pisan entre sí.
    for b in buckets:
        assert b["pronosticado_llegadas_cusco"] <= b["pronosticado"]
        assert b["pronosticado_llegadas_nacionales"] <= b["pronosticado"]
        assert (
            b["pronosticado_llegadas_cusco"] + b["pronosticado_llegadas_nacionales_otras"]
            <= b["pronosticado"]
        )
        assert (
            b["pronosticado_llegadas_nacionales_otras"] <= b["pronosticado_llegadas_nacionales"]
        )
        # Arribos y despegues parten el total sin solaparse.
        assert b["pronosticado_despegues"] <= b["pronosticado"]


def test_flight_history_recorded_on_update():
    client = _fresh_client()
    _login(client, operator_name="GLAGO")
    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"},
    )
    flight_id = resp.json()["id"]

    client.patch(f"/flights/{flight_id}", json={"observaciones": "primera nota"})

    # El operador que firma el cambio sale del turno autenticado, no de un
    # campo del payload -- para simular al siguiente operador FMP se abre un
    # segundo turno con su propio token.
    client2 = _fresh_client()
    _login(client2, operator_name="JMEZA")
    client2.patch(f"/flights/{flight_id}", json={"observaciones": "nota corregida"})

    history = client.get(f"/flights/{flight_id}/history").json()
    assert len(history) == 2
    assert history[0]["new_value"] == "nota corregida"
    assert history[0]["operator_name"] == "JMEZA"
    assert history[1]["old_value"] is None
    assert history[1]["new_value"] == "primera nota"


def test_delete_flight_keeps_audit_log():
    """El borrado en `flights` es permanente y se lleva el historial en
    cascada -- antes de borrar debe quedar una foto del vuelo y de su
    historial en flight_deletion_log, con quién y cuándo lo borró."""
    from app import database
    from app.models import FlightDeletionLog

    client = _fresh_client()
    _login(client, operator_name="GLAGO")
    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"},
    )
    flight_id = resp.json()["id"]
    client.patch(f"/flights/{flight_id}", json={"observaciones": "nota antes de borrar"})

    resp = client.delete(f"/flights/{flight_id}", params={"reason": "Vuelo cargado por error"})
    assert resp.status_code == 204

    db = database.SessionLocal()
    try:
        log = db.query(FlightDeletionLog).filter_by(original_flight_id=flight_id).one()
    finally:
        db.close()
    assert log.vuelo == "LPE2026"
    assert log.sector.value == "SUR"
    assert log.deleted_by == "GLAGO"
    assert log.deletion_reason == "Vuelo cargado por error"
    assert log.flight_snapshot["observaciones"] == "nota antes de borrar"
    assert len(log.history_snapshot) == 1
    assert log.history_snapshot[0]["new_value"] == "nota antes de borrar"


def test_itinerary_upload_leaves_the_grid_alone():
    """Una actualización de la DGAC reemplaza itinerario y no toca la grilla,
    ni siquiera cuando un vuelo ya cargado deja de figurar en el archivo.

    Antes lo marcaba CANCELADO; la regla se retiró porque cancelar un vuelo es
    una decisión del operador FMP."""
    client = _fresh_client()
    _login(client)
    csv_v1 = "call_sign,direction,hora_utc,aerodromo\nLPE2026,ARR,0019,SPZO\n"
    client.post(
        "/itinerary/upload", params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("itin.csv", csv_v1, "text/csv")},
    )
    resp = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    )
    flight_id = resp.json()["id"]
    assert resp.json()["cancelado"] is False

    # DGAC manda una actualización desde la misma fecha, y LPE2026 ya no está
    csv_v2 = "call_sign,direction,hora_utc,aerodromo\nLPE9999,ARR,0019,SPZO\n"
    client.post(
        "/itinerary/upload", params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("itin.csv", csv_v2, "text/csv")},
    )

    resp = client.get("/flights", params={"sector": "SUR", "flight_date": "2026-06-03"})
    flight = next(f for f in resp.json() if f["id"] == flight_id)
    assert flight["cancelado"] is False


def test_export_csv_contains_flight_and_sector_column():
    client = _fresh_client()
    _login(client)
    client.post("/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"})
    client.post("/flights", json={"sector": "NOR", "flight_date": "2026-06-03", "vuelo": "LPE9999"})

    resp = client.get("/flights/export", params={"flight_date": "2026-06-03"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert "LPE2026" in body and "LPE9999" in body
    assert "SECTOR" in body.splitlines()[0]

    resp_sur = client.get("/flights/export", params={"flight_date": "2026-06-03", "sector": "SUR"})
    body_sur = resp_sur.text
    assert "LPE2026" in body_sur and "LPE9999" not in body_sur
