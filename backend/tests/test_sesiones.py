"""La sesión es una fila, no una columna del turno.

El pase vivía en `turno.token_sesion`, y ahí había un hecho 1:N metido en un
solo lugar: cada reenganche rota el pase, así que un turno tiene varias
sesiones a lo largo de su vida y la columna solo podía guardar la última. Las
anteriores se perdían al pisarse, sin dejar registro de cuándo empezó ni
cuándo dejó de valer cada una.

Turno y sesión además tienen ciclos distintos: el turno es un hecho operativo
cerrado y permanente que va a la bitácora; la sesión se emite, se usa, se rota
y se revoca.
"""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import auth, database
from app.models import Sesion, ShiftLog


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _login(client: TestClient, operator_name: str = "GLAGO", position: str = "FMP SUR") -> dict:
    auth._intentos_por_origen.clear()
    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": operator_name, "position": position, "pin": "1234"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sesiones(turno_id: int) -> list[Sesion]:
    with database.SessionLocal() as db:
        return (
            db.query(Sesion)
            .filter(Sesion.turno_id == turno_id)
            .order_by(Sesion.iniciada_en, Sesion.id)
            .all()
        )


def test_iniciar_turno_abre_una_sesion():
    client = _client()
    turno = _login(client)

    sesiones = _sesiones(turno["id"])
    assert len(sesiones) == 1
    sesion = sesiones[0]
    assert sesion.revocada_en is None
    assert sesion.iniciada_en is not None
    assert sesion.operador_id is not None
    # De la base no se puede sacar el pase: solo vive su huella.
    assert sesion.token_hash != turno["session_token"]
    assert sesion.token_hash == auth.huella_token(turno["session_token"])


def test_el_pase_de_la_sesion_autoriza_escrituras():
    client = _client()
    turno = _login(client)
    client.headers["X-Shift-Token"] = turno["session_token"]

    resp = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    )
    assert resp.status_code == 200, resp.text


def test_cada_reenganche_deja_su_sesion_registrada():
    """Lo que la columna no podía guardar: el turno acumula las sesiones y
    cada una dice hasta cuándo valió."""
    client = _client()
    primero = _login(client)
    segundo = _login(client)  # reengancha al mismo turno
    tercero = _login(client)

    assert primero["id"] == segundo["id"] == tercero["id"]

    sesiones = _sesiones(primero["id"])
    assert len(sesiones) == 3

    # las dos primeras revocadas por rotación, la última vigente
    assert [s.revocada_en is None for s in sesiones] == [False, False, True]
    assert {s.motivo_revocacion for s in sesiones[:2]} == {"rotacion"}
    assert sesiones[-1].motivo_revocacion is None


def test_rotar_el_pase_invalida_el_anterior():
    """Volver a entrar con el PIN tiene que invalidar la copia que haya quedado
    en otra computadora."""
    client = _client()
    primero = _login(client)
    viejo = primero["session_token"]

    nuevo = _login(client)["session_token"]
    assert nuevo != viejo

    assert client.get("/shifts/active", headers={"X-Shift-Token": viejo}).status_code == 401
    assert client.get("/shifts/active", headers={"X-Shift-Token": nuevo}).status_code == 200


def test_cerrar_turno_revoca_la_sesion_y_deja_el_motivo():
    client = _client()
    turno = _login(client)
    client.headers["X-Shift-Token"] = turno["session_token"]

    assert client.post(f"/shifts/{turno['id']}/clock-out").status_code == 200

    sesiones = _sesiones(turno["id"])
    assert all(s.revocada_en is not None for s in sesiones)
    assert {s.motivo_revocacion for s in sesiones} == {"cierre_turno"}

    # y el pase ya no sirve
    assert client.get(
        "/shifts/active", headers={"X-Shift-Token": turno["session_token"]}
    ).status_code == 401


def test_la_sesion_revocada_no_se_borra():
    """Es evidencia de un acceso: tiene que quedar registrado que existió y
    hasta cuándo valió."""
    client = _client()
    turno = _login(client)
    client.headers["X-Shift-Token"] = turno["session_token"]
    client.post(f"/shifts/{turno['id']}/clock-out")

    sesiones = _sesiones(turno["id"])
    assert len(sesiones) == 1
    assert sesiones[0].iniciada_en is not None
    assert sesiones[0].revocada_en is not None


def test_un_pase_legado_del_turno_sigue_sirviendo():
    """Los turnos que ya estaban abiertos antes de `ctot.sesion` no tienen
    fila de sesión. Sin el respaldo contra `turno.token_sesion`, desplegar el
    cambio habría invalidado en el acto los pases de todos los turnos en
    curso."""
    from app import clock

    client = _client()
    with database.SessionLocal() as db:
        legado = ShiftLog(
            operator_name="MROMERO",
            position="FMP NOR",
            start_time=clock.now_utc() - timedelta(hours=1),
            last_seen_at=clock.now_utc(),
            session_token=auth.huella_token("pase-de-antes"),
        )
        db.add(legado)
        db.commit()
        legado_id = legado.id

    assert _sesiones(legado_id) == []

    resp = client.get("/shifts/active", headers={"X-Shift-Token": "pase-de-antes"})
    assert resp.status_code == 200, resp.text
    assert legado_id in [t["id"] for t in resp.json()]


def test_la_duracion_ya_no_se_guarda_y_se_calcula():
    """Era una columna con la resta ya hecha. La API la sigue devolviendo."""
    client = _client()
    turno = _login(client)
    client.headers["X-Shift-Token"] = turno["session_token"]

    assert turno["duration_minutes"] is None  # turno abierto

    cerrado = client.post(f"/shifts/{turno['id']}/clock-out").json()
    assert cerrado["duration_minutes"] is not None
    assert cerrado["duration_minutes"] >= 0

    with database.SessionLocal() as db:
        # la columna vieja queda para los turnos cerrados de antes, y los
        # nuevos no la escriben
        assert db.get(ShiftLog, turno["id"]).duracion_original_minutos is None
