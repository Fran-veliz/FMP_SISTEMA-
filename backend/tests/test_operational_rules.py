import pytest
"""Reglas operativas del cuestionario FMP (julio 2026):

- La HORA (hora de la comunicación desde provincia) se registra sola con la
  hora UTC actual al crear el vuelo, y es inmutable junto con el VUELO.
- El cruce con el itinerario DGAC busca también en el día anterior cuando la
  comunicación es de madrugada (0000-0559 UTC), marcando el slot con "D-1".
- La nota de relevo se guarda al cerrar turno y se expone al siguiente
  operador FMP de la misma posición (últimas 24 h).
- /itinerary/lookup valida el call sign en vivo antes de agregar el vuelo.
"""
from datetime import datetime
from unittest import mock

from fastapi.testclient import TestClient


def _fresh_client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _login(
    client: TestClient, operator_name: str = "GLAGO", position: str = "FMP SUR", pin: str = "1234"
) -> dict:
    """Abre turno y deja el token de sesión seteado en el cliente, para que
    las siguientes escrituras (POST/PATCH/DELETE) queden autorizadas."""
    resp = client.post("/shifts/clock-in", json={"operator_name": operator_name, "position": position, "pin": pin})
    assert resp.status_code == 200, resp.text
    shift = resp.json()
    client.headers["X-Shift-Token"] = shift["session_token"]
    return shift


def _upload_csv(client: TestClient, effective_from: str, rows: str, **params):
    csv_content = "call_sign,direction,hora_utc,aerodromo\n" + rows
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": effective_from, "confirmado": True, **params},
        files={"file": ("itin.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_hora_se_autocompleta_con_utc_al_crear():
    client = _fresh_client()
    _login(client)
    resp = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    )
    assert resp.status_code == 200, resp.text
    hora = resp.json()["hora"]
    assert hora is not None and len(hora) == 4 and hora.isdigit()
    # coincide con la hora UTC actual (con 1 minuto de tolerancia)
    # El reloj de la app (corregido por NTP), no el del sistema: es lo que
    # realmente usa create_flight para autocompletar la HORA.
    from app import clock

    ahora = clock.now_utc()
    esperadas = {ahora.strftime("%H%M"), ahora.replace(minute=ahora.minute).strftime("%H%M")}
    assert hora in esperadas or abs(int(hora) - int(ahora.strftime("%H%M"))) <= 1


def test_hora_explicita_se_respeta():
    client = _fresh_client()
    _login(client)
    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026", "hora": "2248"},
    )
    assert resp.json()["hora"] == "2248"


def test_hora_y_vuelo_son_inmutables():
    client = _fresh_client()
    _login(client)
    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026", "hora": "2248"},
    )
    flight_id = resp.json()["id"]

    resp = client.patch(
        f"/flights/{flight_id}",
        json={"hora": "9999", "vuelo": "HACKED", "observaciones": "ok"},
    )
    body = resp.json()
    assert body["hora"] == "2248"
    assert body["vuelo"] == "LPE2026"
    assert body["observaciones"] == "ok"  # el resto del PATCH sí se aplica

    # y no queda rastro en el historial de un cambio que no ocurrió
    history = client.get(f"/flights/{flight_id}/history").json()
    campos = {h["field_name"] for h in history}
    assert "hora" not in campos and "vuelo" not in campos


@pytest.mark.parametrize("hora", ["0000", "0245", "0559"])
def test_slot_dia_anterior_en_madrugada(hora):
    client = _fresh_client()
    _login(client)
    # itinerario cargado para el 2 de junio; el vuelo se registra el 3 de
    # junio con comunicación de madrugada -> debe encontrarse en el D-1
    _upload_csv(client, "2026-06-02", "LAN2371,ARR,0230,SPQU\n")

    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LAN2371", "hora": hora},
    )
    body = resp.json()
    assert body["slot_arr_dgac"] == "0230 D-1"
    assert body["adep"] == "SPQU"


@pytest.mark.parametrize("hora", ["0600", "1500"])
def test_slot_dia_anterior_no_aplica_fuera_de_madrugada(hora):
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-02", "LAN2371,ARR,0230,SPQU\n")

    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LAN2371", "hora": hora},
    )
    assert resp.json()["slot_arr_dgac"] is None


def test_itinerario_de_hoy_gana_sobre_dia_anterior():
    client = _fresh_client()
    _login(client)
    _upload_csv(
        client,
        "2026-06-02",
        "LAN2371,ARR,0230,SPQU\nLAN2371,ARR,0450,SPZO\n",
    )
    # la segunda fila queda en 06-02 igual (CSV usa effective_from como fecha),
    # así que para "hoy gana" cargamos hoy aparte con otra hora
    _upload_csv(client, "2026-06-03", "LAN2371,ARR,0450,SPZO\n")

    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LAN2371", "hora": "0245"},
    )
    body = resp.json()
    assert body["slot_arr_dgac"] == "0450"  # sin sufijo D-1: vino del día actual
    assert body["adep"] == "SPZO"


def test_nota_de_relevo_flujo_completo():
    client = _fresh_client()
    shift = client.post(
        "/shifts/clock-in", json={"operator_name": "SPADILLA", "position": "FMP SUR", "pin": "1234"}
    ).json()

    client.headers["X-Shift-Token"] = shift["session_token"]
    resp = client.post(
        f"/shifts/{shift['id']}/clock-out",
        json={"handover_note": "LPE2026 espera CTOT 2. SKU801 sin ETD."},
    )
    assert resp.status_code == 200
    assert resp.json()["handover_note"] == "LPE2026 espera CTOT 2. SKU801 sin ETD."

    # el relevo de la MISMA posición la ve (abre su turno para consultarla:
    # el clock-out anterior invalidó el token y las lecturas exigen turno)
    _login(client, operator_name="OCRUZ", position="FMP NOR")
    last = client.get("/shifts/last-handover", params={"position": "FMP SUR"}).json()
    assert last is not None and last["operator_name"] == "SPADILLA"

    # otra posición no ve nada
    assert client.get("/shifts/last-handover", params={"position": "FMP NOR"}).json() is None


def test_clock_out_sin_nota_sigue_funcionando():
    client = _fresh_client()
    shift = client.post(
        "/shifts/clock-in", json={"operator_name": "MROMERO", "position": "FMP SUR", "pin": "1234"}
    ).json()

    client.headers["X-Shift-Token"] = shift["session_token"]
    # sin body (compatibilidad con clientes viejos)
    resp = client.post(f"/shifts/{shift['id']}/clock-out")
    assert resp.status_code == 200
    assert resp.json()["handover_note"] is None
    # El clock-out invalida el token, y las lecturas ahora exigen turno: el
    # siguiente operador FMP abre el suyo para consultar la nota de relevo.
    _login(client, operator_name="OCRUZ")
    assert client.get("/shifts/last-handover", params={"position": "FMP SUR"}).json() is None


def test_ultima_nota_gana_aunque_haya_cierres_sin_nota():
    client = _fresh_client()
    s1 = client.post(
        "/shifts/clock-in", json={"operator_name": "SPADILLA", "position": "FMP SUR", "pin": "1234"}
    ).json()
    client.headers["X-Shift-Token"] = s1["session_token"]
    client.post(f"/shifts/{s1['id']}/clock-out", json={"handover_note": "pendiente A"})
    s2 = client.post(
        "/shifts/clock-in", json={"operator_name": "MROMERO", "position": "FMP SUR", "pin": "1234"}
    ).json()
    client.headers["X-Shift-Token"] = s2["session_token"]
    client.post(f"/shifts/{s2['id']}/clock-out")  # cierra sin nota

    _login(client, operator_name="OCRUZ")
    last = client.get("/shifts/last-handover", params={"position": "FMP SUR"}).json()
    assert last["operator_name"] == "SPADILLA"
    assert last["handover_note"] == "pendiente A"


def test_lookup_en_vivo_hoy_y_sin_coincidencia():
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1545,SPZO\n")

    body = client.get(
        "/itinerary/lookup", params={"flight_date": "2026-06-03", "call_sign": "LPE2026"}
    ).json()
    assert body == {"found": True, "fuente": "hoy", "hora_utc": "1545", "aerodromo": "SPZO"}

    body = client.get(
        "/itinerary/lookup", params={"flight_date": "2026-06-03", "call_sign": "FAP001"}
    ).json()
    assert body["found"] is False


def test_lookup_en_vivo_normaliza_minusculas():
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1545,SPZO\n")
    body = client.get(
        "/itinerary/lookup", params={"flight_date": "2026-06-03", "call_sign": "lpe2026"}
    ).json()
    assert body["found"] is True


def test_carga_de_itinerario_exige_confirmacion():
    """Soltar el archivo no puede aplicar nada: sin confirmado=true el
    itinerario queda intacto. Es la protección contra cargar un archivo
    equivocado, que antes se aplicaba de una."""
    client = _fresh_client()
    _login(client)
    csv_content = "call_sign,direction,hora_utc,aerodromo\nLPE2026,ARR,1545,SPZO\n"

    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03"},
        files={"file": ("itin.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 400
    assert "confirmar" in resp.json()["detail"].lower()

    # nada se cargó ni quedó registrado en el historial
    assert client.get("/itinerary/uploads").json() == []
    lookup = client.get(
        "/itinerary/lookup", params={"flight_date": "2026-06-03", "call_sign": "LPE2026"}
    ).json()
    assert lookup["found"] is False


def test_preview_no_toca_el_itinerario():
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1545,SPZO\n")

    # una actualización que borraría LPE2026: la vista previa la anuncia pero
    # no la aplica.
    csv_v2 = "call_sign,direction,hora_utc,aerodromo\nLPE9999,ARR,0800,SPZO\n"
    resp = client.post(
        "/itinerary/preview",
        params={"effective_from": "2026-06-03"},
        files={"file": ("itin.csv", csv_v2, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    preview = resp.json()
    assert preview["formato"] == "csv"
    assert preview["filas_aceptadas"] == 1
    assert preview["fecha_min"] == "2026-06-03" and preview["fecha_max"] == "2026-06-03"
    assert preview["por_fecha"] == [{"fecha": "2026-06-03", "arribos": 1, "salidas": 0}]
    assert preview["filas_reemplazadas"] == 1
    assert preview["dias_reemplazados"] == 1

    # el itinerario viejo sigue intacto y el nuevo no entró
    assert client.get(
        "/itinerary/lookup", params={"flight_date": "2026-06-03", "call_sign": "LPE2026"}
    ).json()["found"] is True
    assert client.get(
        "/itinerary/lookup", params={"flight_date": "2026-06-03", "call_sign": "LPE9999"}
    ).json()["found"] is False
    # la vista previa tampoco deja rastro en el historial de actualizaciones
    assert len(client.get("/itinerary/uploads").json()) == 1


def test_preview_avisa_de_los_vuelos_que_quedan_sin_itinerario():
    """La vista previa informa qué vuelos de la grilla quedan sin itinerario,
    para que el operador sepa qué revisar. No escribe nada."""
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,0019,SPZO\n")
    client.post("/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"})

    csv_v2 = "call_sign,direction,hora_utc,aerodromo\nLPE9999,ARR,0019,SPZO\n"
    preview = client.post(
        "/itinerary/preview",
        params={"effective_from": "2026-06-03"},
        files={"file": ("itin.csv", csv_v2, "text/csv")},
    ).json()

    assert preview["vuelos_sin_itinerario"] == ["2026-06-03 LPE2026"]
    assert any("no figuran en este archivo" in a for a in preview["advertencias"])

    # y el vuelo sigue sin cancelar: la vista previa no toca la base
    vuelos = client.get("/flights", params={"sector": "SUR", "flight_date": "2026-06-03"}).json()
    assert vuelos[0]["cancelado"] is False


def test_preview_avisa_cuando_el_archivo_no_trae_llegadas():
    """El caso real que motivó la vista previa: se subió una planilla de
    salidas ya operada como si fuera el itinerario del día."""
    client = _fresh_client()
    _login(client)
    csv_content = (
        "call_sign,direction,hora_utc,aerodromo\n"
        "AVA48,DEP,0640,SKBO\n"
        "LAN2470,DEP,1200,SCEL\n"
    )
    preview = client.post(
        "/itinerary/preview",
        params={"effective_from": "2026-06-03"},
        files={"file": ("AGOSTO28 CONSOLIDADO.csv", csv_content, "text/csv")},
    ).json()

    avisos = " | ".join(preview["advertencias"])
    assert "ninguna llegada" in avisos
    assert "TIPO DE AERONAVE" in avisos
    assert "2026-06-03" in avisos  # avisa que todas las filas van a esa fecha


def test_preview_avisa_si_dejaria_el_itinerario_vacio():
    client = _fresh_client()
    _login(client)
    csv_content = "call_sign,direction,hora_utc\nLPE2026,XXX,1545\n"
    preview = client.post(
        "/itinerary/preview",
        params={"effective_from": "2026-06-03"},
        files={"file": ("itin.csv", csv_content, "text/csv")},
    ).json()

    assert preview["filas_aceptadas"] == 0
    assert preview["filas_rechazadas"] == 1
    assert any("VACÍO" in a for a in preview["advertencias"])


def test_carga_por_dia_no_toca_los_dias_siguientes():
    """Alcance "dia": subir un día suelto no puede borrar el itinerario ya
    cargado de los días posteriores."""
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1000,SPZO\n")
    _upload_csv(client, "2026-06-04", "LPE3030,ARR,1100,SPZO\n")

    # se recarga solo el 03; el 04 tiene que sobrevivir
    _upload_csv(client, "2026-06-03", "LPE7777,ARR,1200,SPZO\n", alcance="dia")

    def esta(fecha, vuelo):
        return client.get(
            "/itinerary/lookup", params={"flight_date": fecha, "call_sign": vuelo}
        ).json()["found"]

    assert esta("2026-06-03", "LPE7777") is True
    assert esta("2026-06-03", "LPE2026") is False  # reemplazado, como corresponde
    assert esta("2026-06-04", "LPE3030") is True   # intacto


def test_carga_desde_sigue_borrando_los_dias_siguientes():
    """El alcance por defecto no cambia: la actualización DGAC de temporada
    sigue reemplazando de la fecha en adelante."""
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1000,SPZO\n")
    _upload_csv(client, "2026-06-04", "LPE3030,ARR,1100,SPZO\n")

    _upload_csv(client, "2026-06-03", "LPE7777,ARR,1200,SPZO\n")

    assert client.get(
        "/itinerary/lookup", params={"flight_date": "2026-06-04", "call_sign": "LPE3030"}
    ).json()["found"] is False


def test_la_carga_de_itinerario_no_cancela_vuelos():
    """Un vuelo que deja de figurar en el itinerario NO se marca cancelado.

    Hubo una regla que lo hacía, con un tilde para desactivarla. Se retiró:
    cancelar un vuelo es una decisión del operador FMP, no la consecuencia de
    que un archivo de la DGAC lo omita. La carga reemplaza itinerario y nada
    más, con cualquier alcance."""
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,0019,SPZO\n")
    resp = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    )
    flight_id = resp.json()["id"]

    # Con alcance "desde", que es el de la actualización de temporada: el que
    # antes sí cancelaba.
    _upload_csv(client, "2026-06-03", "LPE9999,ARR,0019,SPZO\n")

    vuelos = client.get(
        "/flights", params={"sector": "SUR", "flight_date": "2026-06-03"}
    ).json()
    vuelo = next(f for f in vuelos if f["id"] == flight_id)
    assert vuelo["cancelado"] is False


def test_la_carga_de_itinerario_no_descancela_vuelos():
    """La contracara: un vuelo cancelado a mano no se "descancela" solo porque
    reaparezca en el archivo. La carga no escribe en la grilla en ningún
    sentido."""
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,0019,SPZO\n")
    resp = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    )
    flight_id = resp.json()["id"]
    client.patch(f"/flights/{flight_id}", json={"cancelado": True})

    _upload_csv(client, "2026-06-03", "LPE2026,ARR,0019,SPZO\n")

    vuelos = client.get(
        "/flights", params={"sector": "SUR", "flight_date": "2026-06-03"}
    ).json()
    assert next(f for f in vuelos if f["id"] == flight_id)["cancelado"] is True


def test_historial_registra_el_alcance():
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1000,SPZO\n", alcance="dia")
    u = client.get("/itinerary/uploads").json()[0]
    assert u["alcance"] == "dia"


def test_preview_refleja_el_alcance_y_avisa_de_los_vuelos_sin_itinerario():
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,0019,SPZO\n")
    _upload_csv(client, "2026-06-04", "LPE3030,ARR,1100,SPZO\n")
    client.post("/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"})

    csv_v2 = "call_sign,direction,hora_utc,aerodromo\nLPE9999,ARR,0019,SPZO\n"

    # con alcance "dia" solo se reemplaza ese día
    preview = client.post(
        "/itinerary/preview",
        params={"effective_from": "2026-06-03", "alcance": "dia"},
        files={"file": ("itin.csv", csv_v2, "text/csv")},
    ).json()
    assert preview["alcance"] == "dia"
    assert preview["dias_reemplazados"] == 1  # solo el 03, no el 04

    # el mismo archivo con el alcance por defecto toca los dos días
    preview = client.post(
        "/itinerary/preview",
        params={"effective_from": "2026-06-03"},
        files={"file": ("itin.csv", csv_v2, "text/csv")},
    ).json()
    assert preview["dias_reemplazados"] == 2

    # el vuelo de la grilla queda sin itinerario: se informa y se avisa, pero
    # la vista previa no promete ningún cambio sobre él
    assert preview["vuelos_sin_itinerario"] == ["2026-06-03 LPE2026"]
    assert any("NO los modifica" in a for a in preview["advertencias"])


def test_historial_de_actualizaciones_del_itinerario():
    client = _fresh_client()
    _login(client, operator_name="SPADILLA")
    assert client.get("/itinerary/uploads").json() == []

    csv_content = "call_sign,direction,hora_utc,aerodromo\nLPE2026,ARR,1545,SPZO\n"
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("W25_v2.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200

    uploads = client.get("/itinerary/uploads").json()
    assert len(uploads) == 1
    u = uploads[0]
    assert u["uploaded_by"] == "SPADILLA"
    assert u["filename"] == "W25_v2.csv"
    assert u["effective_from"] == "2026-06-03"
    assert u["filas_aceptadas"] == 1 and u["filas_rechazadas"] == 0

    # una segunda actualización, de otro operador FMP, queda primera en la
    # lista (más reciente) -- uploaded_by sale del turno autenticado.
    _login(client, operator_name="MROMERO")
    client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-05", "confirmado": True},
        files={"file": ("W25_v3.csv", csv_content, "text/csv")},
    )
    uploads = client.get("/itinerary/uploads").json()
    assert len(uploads) == 2
    assert uploads[0]["uploaded_by"] == "MROMERO"
    assert uploads[0]["effective_from"] == "2026-06-05"


@pytest.mark.reloj_real
def test_lookup_dia_anterior_solo_en_madrugada():
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-02", "LAN2371,ARR,0230,SPQU\n")

    with mock.patch("app.clock.datetime") as m:
        m.utcnow.return_value = datetime(2026, 6, 3, 5, 59)  # último minuto de madrugada
        body = client.get(
            "/itinerary/lookup", params={"flight_date": "2026-06-03", "call_sign": "LAN2371"}
        ).json()
        assert body["found"] is True and body["fuente"] == "dia_anterior"

        m.utcnow.return_value = datetime(2026, 6, 3, 6, 0)  # 0600 UTC: ya no aplica
        body = client.get(
            "/itinerary/lookup", params={"flight_date": "2026-06-03", "call_sign": "LAN2371"}
        ).json()
        assert body["found"] is False
