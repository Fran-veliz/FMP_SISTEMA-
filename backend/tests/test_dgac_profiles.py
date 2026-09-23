"""Perfiles de consulta de la DGAC, límites de alcance y bloqueo por intentos.

Cubre lo que pidió el área (2026-07-31):
  - dos perfiles DGAC con permisos distintos de descarga,
  - ambos limitados al año en curso,
  - el principal con ventana de semanas para descargar,
  - bloqueo del perfil tras 10 intentos fallidos de PIN,
  - las lecturas ya no son anónimas.
"""
from datetime import date

from fastapi.testclient import TestClient

from app import clock
from app.auth import MAX_FAILED_ATTEMPTS
from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def _login(client: TestClient, name: str, pin: str, position: str = "FMP SUR") -> dict:
    resp = client.post(
        "/shifts/clock-in", json={"operator_name": name, "position": position, "pin": pin}
    )
    assert resp.status_code == 200, resp.text
    shift = resp.json()
    client.headers["X-Shift-Token"] = shift["session_token"]
    return shift


# --------------------------------------------------------------- lecturas


def test_lecturas_exigen_turno():
    """Antes de la v3.1 cualquiera con acceso al puerto podía bajarse la
    grilla completa sin identificarse (ver DECISIONES.md §5)."""
    client = _client()
    hoy = clock.now_utc().date().isoformat()
    for path, params in (
        ("/flights", {"sector": "SUR", "flight_date": hoy}),
        ("/flights/export", {"flight_date": hoy}),
        ("/shifts/active", {}),
        ("/forecast", {"flight_date": hoy}),
    ):
        assert client.get(path, params=params).status_code == 401, path


# ------------------------------------------------------- perfiles de DGAC


def test_dgac_principal_descarga_dentro_de_la_ventana():
    client = _client()
    _login(client, "DGAC1", "2580")
    hoy = clock.now_utc().date()
    resp = client.get("/flights/export", params={"flight_date": hoy.isoformat()})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


def test_dgac_principal_descarga_cualquier_dia_seleccionado():
    """Decisión del área (2026-07-31): el principal descarga el día que tenga
    seleccionado, sin ventana de semanas ni límite de año."""
    client = _client()
    _login(client, "DGAC1", "2580")
    for fecha in (
        clock.now_utc().date().isoformat(),
        date(clock.now_utc().year, 1, 2).isoformat(),
        date(clock.now_utc().year - 2, 6, 3).isoformat(),
    ):
        resp = client.get("/flights/export", params={"flight_date": fecha})
        assert resp.status_code == 200, f"{fecha}: {resp.text}"


def test_dgac_secundario_ve_pero_no_descarga():
    client = _client()
    _login(client, "DGAC2", "2581")
    hoy = clock.now_utc().date().isoformat()

    # ver sí
    assert client.get("/flights", params={"sector": "SUR", "flight_date": hoy}).status_code == 200
    # descargar no, ni siquiera del día de hoy
    resp = client.get("/flights/export", params={"flight_date": hoy})
    assert resp.status_code == 403
    assert "no descargar" in resp.json()["detail"]


def test_dgac_secundario_solo_ve_el_ano_en_curso():
    client = _client()
    _login(client, "DGAC2", "2581")
    anio_pasado = date(clock.now_utc().year - 1, 6, 3).isoformat()
    resp = client.get("/flights", params={"sector": "SUR", "flight_date": anio_pasado})
    assert resp.status_code == 403
    assert str(clock.now_utc().year) in resp.json()["detail"]


def test_dgac_principal_no_tiene_limite_de_ano():
    client = _client()
    _login(client, "DGAC1", "2580")
    anio_pasado = date(clock.now_utc().year - 1, 6, 3).isoformat()
    assert client.get("/flights", params={"sector": "SUR", "flight_date": anio_pasado}).status_code == 200


def test_dgac_no_puede_escribir():
    """El perfil de solo lectura sigue sin poder modificar nada."""
    client = _client()
    _login(client, "DGAC1", "2580")
    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": clock.now_utc().date().isoformat(), "vuelo": "LPE9999"},
    )
    assert resp.status_code == 403


def test_operador_fmp_no_tiene_esos_limites():
    client = _client()
    _login(client, "SPADILLA", "1234")
    anio_pasado = date(clock.now_utc().year - 1, 6, 3).isoformat()
    assert client.get("/flights", params={"sector": "SUR", "flight_date": anio_pasado}).status_code == 200
    assert client.get("/flights/export", params={"flight_date": anio_pasado}).status_code == 200


# ------------------------------------------------------------- bloqueo


def test_perfil_se_bloquea_tras_diez_intentos_fallidos():
    client = _client()
    for _ in range(MAX_FAILED_ATTEMPTS):
        resp = client.post(
            "/shifts/clock-in", json={"operator_name": "DGAC1", "position": "FMP SUR", "pin": "0000"}
        )
        assert resp.status_code == 401

    # el intento siguiente ya no compara el PIN: rebota bloqueado, incluso
    # con el PIN correcto
    resp = client.post(
        "/shifts/clock-in", json={"operator_name": "DGAC1", "position": "FMP SUR", "pin": "2580"}
    )
    assert resp.status_code == 429
    assert "bloqueado" in resp.json()["detail"].lower()


def test_un_ingreso_correcto_limpia_el_contador():
    client = _client()
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        client.post(
            "/shifts/clock-in", json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "0000"}
        )
    # acierta antes de llegar al límite
    shift = _login(client, "GLAGO", "1234")
    client.post(f"/shifts/{shift['id']}/clock-out")

    # el contador quedó en cero: vuelven a hacer falta 10 fallos
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        resp = client.post(
            "/shifts/clock-in", json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "0000"}
        )
        assert resp.status_code == 401, "se bloqueó antes de tiempo"


def test_el_bloqueo_no_delata_nombres_validos():
    """Un nombre inexistente responde igual que un PIN equivocado."""
    client = _client()
    a = client.post(
        "/shifts/clock-in", json={"operator_name": "NO EXISTE", "position": "FMP SUR", "pin": "1234"}
    )
    b = client.post(
        "/shifts/clock-in", json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "0000"}
    )
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_reenganche_refresca_los_limites_del_perfil():
    """Regresión (detectada en verificación en vivo, 2026-07-31): al
    reenganchar un turno ya abierto se devolvía el turno tal cual estaba, con
    los permisos que tenía al abrirlo. Un turno anterior a la restricción
    seguía consultando cualquier año hasta que esa persona cerrara turno."""
    from app import database
    from app.models import ShiftLog

    client = _client()
    shift = _login(client, "DGAC2", "2581")

    # Se simula un turno abierto de antes de la restricción.
    db = database.SessionLocal()
    try:
        fila = db.get(ShiftLog, shift["id"])
        fila.can_export = True
        db.commit()
    finally:
        db.close()

    hoy = clock.now_utc().date().isoformat()
    assert client.get("/flights/export", params={"flight_date": hoy}).status_code == 200

    # Al volver a entrar, el turno recupera los límites del perfil.
    _login(client, "DGAC2", "2581")
    assert client.get("/flights/export", params={"flight_date": hoy}).status_code == 403
