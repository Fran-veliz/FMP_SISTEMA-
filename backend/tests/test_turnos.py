"""Máximo de horas por posición (auth.SHIFT_MAX_HOURS).

Los relevos de la FMP son cada ~2 h, así que un turno de más de 3 h casi
siempre significa que el operador se fue y se olvidó de cerrarlo -- así
quedaron turnos abiertos durante semanas sosteniendo un token vivo.

La regla AVISA, no corta: expulsar a alguien de una posición en plena
operación 24/7 es peor que el problema que evita.
"""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import clock, database
from app.auth import SHIFT_MAX_HOURS
from app.main import app
from app.models import ShiftLog


def _client() -> TestClient:
    return TestClient(app)


def _login(client: TestClient, name: str = "SPADILLA") -> dict:
    resp = client.post(
        "/shifts/clock-in", json={"operator_name": name, "position": "FMP SUR", "pin": "1234"}
    )
    assert resp.status_code == 200, resp.text
    shift = resp.json()
    client.headers["X-Shift-Token"] = shift["session_token"]
    return shift


def _abrir_hace(shift_id: int, horas: float) -> None:
    """Simula que el turno se abrió hace ese tiempo, sin tocar la actividad:
    lo que se mide acá es la duración del turno, no la inactividad."""
    db = database.SessionLocal()
    try:
        fila = db.get(ShiftLog, shift_id)
        fila.start_time = clock.now_utc() - timedelta(hours=horas)
        db.commit()
    finally:
        db.close()


def _turno_activo(client: TestClient, shift_id: int) -> dict:
    activos = client.get("/shifts/active").json()
    return next(s for s in activos if s["id"] == shift_id)


def test_un_turno_recien_abierto_no_excede():
    client = _client()
    shift = _login(client)
    actual = _turno_activo(client, shift["id"])
    assert actual["excede_maximo"] is False
    assert actual["horas_en_turno"] < 1
    assert actual["maximo_horas"] == SHIFT_MAX_HOURS


def test_justo_por_debajo_del_maximo_no_excede():
    client = _client()
    shift = _login(client)
    _abrir_hace(shift["id"], SHIFT_MAX_HOURS - 0.5)
    assert _turno_activo(client, shift["id"])["excede_maximo"] is False


def test_pasado_el_maximo_queda_marcado():
    client = _client()
    shift = _login(client)
    _abrir_hace(shift["id"], SHIFT_MAX_HOURS + 1)

    actual = _turno_activo(client, shift["id"])
    assert actual["excede_maximo"] is True
    assert actual["horas_en_turno"] > SHIFT_MAX_HOURS


def test_exceder_el_maximo_no_corta_la_sesion():
    """El punto de la regla: avisa, no expulsa. Un operador que sigue en
    posición tiene que poder trabajar aunque el turno esté excedido."""
    client = _client()
    shift = _login(client)
    _abrir_hace(shift["id"], SHIFT_MAX_HOURS + 20)

    hoy = clock.now_utc().date().isoformat()
    resp = client.get("/flights", params={"sector": "SUR", "flight_date": hoy})
    assert resp.status_code == 200
    assert _turno_activo(client, shift["id"])["excede_maximo"] is True


# --------------------------------------------------------------------------- #
# La bitácora por jornada
#
# "¿Quién estuvo ayer y a qué hora?" se responde con GET /shifts?shift_date=.
# El filtro miraba solo la hora de INICIO, así que un relevo que entró a las
# 22:00 y cerró a las 02:00 figuraba únicamente en el día que empezó: al
# consultar el día siguiente, sus dos primeras horas aparecían sin nadie a
# cargo. Un turno tiene que listarse en todas las jornadas que cubre.
# --------------------------------------------------------------------------- #

def _turno_entre(client: TestClient, inicio, fin, nombre: str = "SPADILLA") -> int:
    """Deja registrado un turno ya cerrado con ese horario exacto."""
    from app.models import Position

    db = database.SessionLocal()
    try:
        fila = ShiftLog(
            operator_name=nombre,
            position=Position.FMP_SUR,
            start_time=inicio,
            end_time=fin,
            read_only=False,
        )
        db.add(fila)
        db.commit()
        return fila.id
    finally:
        db.close()


def _jornada(client: TestClient, dia) -> list[dict]:
    resp = client.get("/shifts", params={"shift_date": dia.isoformat()})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_un_turno_que_cruza_la_medianoche_figura_en_los_dos_dias():
    from datetime import datetime

    client = _client()
    _login(client)

    ayer = clock.now_utc().date() - timedelta(days=1)
    anteayer = ayer - timedelta(days=1)
    # 22:00 de anteayer a 02:00 de ayer.
    cruzado = _turno_entre(
        client,
        datetime.combine(anteayer, datetime.min.time()) + timedelta(hours=22),
        datetime.combine(ayer, datetime.min.time()) + timedelta(hours=2),
        nombre="NOCTURNO",
    )

    ids_anteayer = [t["id"] for t in _jornada(client, anteayer)]
    ids_ayer = [t["id"] for t in _jornada(client, ayer)]

    assert cruzado in ids_anteayer, "falta en la jornada en que empezó"
    assert cruzado in ids_ayer, "falta en la jornada que cubrió de madrugada"


def test_un_turno_no_aparece_en_jornadas_que_no_cubre():
    from datetime import datetime

    client = _client()
    _login(client)

    hoy = clock.now_utc().date()
    hace_tres = hoy - timedelta(days=3)
    # Un turno normal, dentro de un solo día.
    dentro = _turno_entre(
        client,
        datetime.combine(hace_tres, datetime.min.time()) + timedelta(hours=8),
        datetime.combine(hace_tres, datetime.min.time()) + timedelta(hours=10),
        nombre="DIURNO",
    )

    assert dentro in [t["id"] for t in _jornada(client, hace_tres)]
    assert dentro not in [t["id"] for t in _jornada(client, hace_tres - timedelta(days=1))]
    assert dentro not in [t["id"] for t in _jornada(client, hace_tres + timedelta(days=1))]


def test_la_bitacora_respeta_el_limite_de_anio_del_perfil():
    """Un perfil acotado no puede recorrer la bitácora de otros años.

    El límite se aplicaba en la grilla, la exportación y el pronóstico, pero
    no acá: por la bitácora se podía pedir cualquier jornada y ver quién
    trabajó, que es dato de personal.
    """
    from datetime import date

    client = _client()
    # DGAC2 es el perfil acotado al año en curso (ver seed_data.DGAC_PROFILES);
    # el PIN es el de la siembra de pruebas, igual que en test_dgac_profiles.
    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": "DGAC2", "position": "FMP SUR", "pin": "2581"},
    )
    assert resp.status_code == 200, resp.text
    turno = resp.json()
    client.headers["X-Shift-Token"] = turno["session_token"]
    assert turno["view_year_only"], "DGAC2 debería estar acotado al año en curso"

    resp = client.get("/shifts", params={"shift_date": date(2021, 5, 3).isoformat()})
    assert resp.status_code == 403, resp.text
