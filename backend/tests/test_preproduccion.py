"""Regresiones de seguridad y operación encontradas en la auditoría de entrega."""
import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, clock, database
from app.main import app
from app.models import Controller, Sesion
from scripts.rotar_pin import rotar


@pytest.mark.parametrize("revocar", [True, False])
def test_rotar_pin_respeta_revocacion_de_sesion(revocar):
    auth._intentos_por_origen.clear()
    client = TestClient(app)
    login = client.post("/shifts/clock-in", json={
        "operator_name": "GLAGO", "position": "FMP SUR", "pin": "1234",
    }).json()
    with database.SessionLocal() as db:
        perfil = db.query(Controller).filter_by(usuario="GLAGO").one()
        rotar(db, perfil, "87654321", cerrar_turnos=revocar)
        db.commit()
        sesion = db.query(Sesion).filter_by(turno_id=login["id"]).one()
        assert (sesion.revocada_en is not None) is revocar
        assert auth.turno_sigue_vigente(db, login["session_token"]) is not revocar
    response = client.get("/shifts/active", headers={"X-Shift-Token": login["session_token"]})
    assert response.status_code == (401 if revocar else 200)


def test_componentes_postgres_con_caracteres_reservados():
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.update(POSTGRES_USER="usuario@corp", POSTGRES_PASSWORD="a@b:/%?#$!",
               POSTGRES_DB="ensayo", POSTGRES_HOST="db", POSTGRES_PORT="5432")
    script = """
import os
from sqlalchemy.engine import make_url
from app.database import DATABASE_URL
url = make_url(DATABASE_URL)
assert url.host == 'db'
assert url.username == os.environ['POSTGRES_USER']
assert url.password == os.environ['POSTGRES_PASSWORD']
assert url.database == 'ensayo'
"""
    process = subprocess.run([sys.executable, "-c", script], env=env,
                             cwd=Path(__file__).resolve().parents[1],
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stderr


def test_sin_entorno_no_publica_documentacion():
    env = os.environ.copy()
    env.pop("ENVIRONMENT", None)
    process = subprocess.run([sys.executable, "-c",
        "from app.main import app; assert app.docs_url is None; "
        "assert app.redoc_url is None; assert app.openapi_url is None"],
        env=env, cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stderr


def test_ntp_usa_offset_del_intercambio(monkeypatch):
    class Client:
        def request(self, *args, **kwargs):
            return SimpleNamespace(offset=-0.125, tx_time=9999999999)
    monkeypatch.setattr(clock.ntplib, "NTPClient", Client)
    offset, server, error = clock._fetch_ntp_offset()
    assert offset == timedelta(seconds=-0.125)
    assert server and error is None


def test_cliente_ws_lento_no_impide_entrega_a_otro(monkeypatch):
    from app import ws
    monkeypatch.setattr(ws, "SEND_TIMEOUT_SECONDS", 0.01)

    class Socket:
        def __init__(self, slow=False):
            self.slow, self.messages, self.closed = slow, [], None
        async def send_json(self, message):
            if self.slow:
                await asyncio.Event().wait()
            self.messages.append(message)
        async def close(self, code):
            self.closed = code

    manager = ws.ConnectionManager()
    slow, healthy = Socket(True), Socket()
    manager._connections = [slow, healthy]
    asyncio.run(asyncio.wait_for(manager.broadcast({"type": "test"}), timeout=1))
    assert healthy.messages == [{"type": "test"}]
    assert slow not in manager._connections and slow.closed == 1013


def test_error_bd_cierra_ws_en_vez_de_abandonar_vigilancia(monkeypatch):
    from app.routers import flight_socket
    monkeypatch.setattr(flight_socket, "SEGUNDOS_REVALIDACION_WS", 0)
    def fail():
        raise RuntimeError("base no disponible")
    monkeypatch.setattr(flight_socket, "SessionLocal", fail)
    class Socket:
        code = None
        async def close(self, code):
            self.code = code
    socket = Socket()
    asyncio.run(asyncio.wait_for(
        flight_socket._vigilar_vigencia_del_turno(socket, "test"), timeout=1))
    assert socket.code == 1011
