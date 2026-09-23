"""Quien hizo un cambio se identifica por ID, no por el nombre que escribió.

Seis columnas guardaban el nombre del operador como texto: turno, historial,
autoría del vuelo, eliminación y las dos de carga. Corregir la grafía de un
nombre en la nómina dejaba las seis desactualizadas y sin forma de saber a
quién se referían.

Estas pruebas fijan que todo lo que se escribe de acá en adelante lleva la
referencia, y que el texto se conserva igual -- es la evidencia de lo que
quedó registrado en su momento, y para el histórico no siempre hay una fila de
`operador` a la que apuntar.
"""
from fastapi.testclient import TestClient

from app import database
from app.models import (
    Controller,
    Flight,
    FlightDeletionLog,
    FlightHistory,
    Importacion,
    ShiftLog,
)


def _fresh_client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _login(client: TestClient, operator_name: str = "GLAGO") -> dict:
    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": operator_name, "position": "FMP SUR", "pin": "1234"},
    )
    assert resp.status_code == 200, resp.text
    shift = resp.json()
    client.headers["X-Shift-Token"] = shift["session_token"]
    return shift


def _id_de(usuario: str) -> int:
    with database.SessionLocal() as db:
        return db.query(Controller).filter(Controller.usuario == usuario).one().id


def test_el_turno_guarda_la_referencia_al_operador():
    client = _fresh_client()
    shift = _login(client, "GLAGO")

    with database.SessionLocal() as db:
        turno = db.get(ShiftLog, shift["id"])
        assert turno.operador_id == _id_de("GLAGO")
        # el texto se conserva: es lo que se registró en su momento
        assert turno.operator_name == "GLAGO"


def test_el_reenganche_completa_la_referencia_de_un_turno_viejo():
    """Un turno abierto de antes de esta columna no la tiene. Se completa al
    reenganchar, sin esperar a que la persona cierre turno."""
    client = _fresh_client()
    shift = _login(client, "GLAGO")

    with database.SessionLocal() as db:
        turno = db.get(ShiftLog, shift["id"])
        turno.operador_id = None
        db.commit()

    _login(client, "GLAGO")  # reengancha al mismo turno

    with database.SessionLocal() as db:
        assert db.get(ShiftLog, shift["id"]).operador_id == _id_de("GLAGO")


def test_el_vuelo_y_su_historial_guardan_la_referencia():
    client = _fresh_client()
    _login(client, "GLAGO")
    esperado = _id_de("GLAGO")

    creado = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    ).json()
    client.patch(f"/flights/{creado['id']}", json={"observaciones": "revisado"})

    with database.SessionLocal() as db:
        vuelo = db.get(Flight, creado["id"])
        assert vuelo.updated_by_id == esperado
        assert vuelo.updated_by == "GLAGO"

        cambios = db.query(FlightHistory).filter(FlightHistory.flight_id == creado["id"]).all()
        assert cambios, "el PATCH tenia que dejar historial"
        assert all(c.operador_id == esperado for c in cambios)
        assert all(c.operator_name == "GLAGO" for c in cambios)


def test_la_eliminacion_guarda_la_referencia_de_quien_borro():
    client = _fresh_client()
    _login(client, "GLAGO")
    creado = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    ).json()

    resp = client.delete(f"/flights/{creado['id']}", params={"reason": "no opero"})
    assert resp.status_code == 204, resp.text

    with database.SessionLocal() as db:
        registro = (
            db.query(FlightDeletionLog)
            .filter(FlightDeletionLog.original_flight_id == creado["id"])
            .one()
        )
        assert registro.deleted_by_id == _id_de("GLAGO")
        assert registro.deleted_by == "GLAGO"


def test_la_carga_de_itinerario_guarda_la_referencia():
    client = _fresh_client()
    _login(client, "GLAGO")

    csv_content = "call_sign,direction,hora_utc,aerodromo\nLPE2026,ARR,1545,SPZO\n"
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("itin.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text

    with database.SessionLocal() as db:
        carga = db.query(Importacion).one()
        assert carga.cargado_por_id == _id_de("GLAGO")
        assert carga.cargado_por == "GLAGO"


def test_dos_operadores_distintos_quedan_atribuidos_a_cada_uno():
    """El caso que el texto no podia distinguir bien: dos personas tocando el
    mismo vuelo."""
    client = _fresh_client()
    _login(client, "GLAGO")
    creado = client.post(
        "/flights", json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"}
    ).json()

    # cierra turno y entra otra persona
    shift = client.get("/shifts/active").json()[0]
    client.post(f"/shifts/{shift['id']}/clock-out")
    _login(client, "OCRUZ")
    client.patch(f"/flights/{creado['id']}", json={"observaciones": "lo reviso otro"})

    with database.SessionLocal() as db:
        vuelo = db.get(Flight, creado["id"])
        assert vuelo.updated_by_id == _id_de("OCRUZ")

        autores = {
            c.operador_id
            for c in db.query(FlightHistory).filter(FlightHistory.flight_id == creado["id"]).all()
        }
        assert _id_de("OCRUZ") in autores
