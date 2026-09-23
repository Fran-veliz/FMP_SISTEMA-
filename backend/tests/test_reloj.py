"""Verificación del reloj contra NTP.

La hora UTC es el producto principal del sistema: si el reloj del servidor
está corrido, se corren la HORA de los vuelos, los CTOT, los timestamps de
turno y el aviso de turno excedido. Por eso se verifica contra NTP y, sobre
todo, por eso la falla de esa verificación tiene que ser visible.
"""
from fastapi.testclient import TestClient

from app import clock
from app.main import app


def test_por_defecto_usa_los_servidores_publicos(monkeypatch):
    monkeypatch.delenv("NTP_SERVERS", raising=False)
    assert clock._configured_servers() == [
        "time.cloudflare.com",
        "pool.ntp.org",
        "time.windows.com",
    ]


def test_se_puede_apuntar_al_ntp_interno(monkeypatch):
    """El caso de CORPAC: red sin salida a internet, NTP propio."""
    monkeypatch.setenv("NTP_SERVERS", "ntp1.corpac.local, ntp2.corpac.local")
    assert clock._configured_servers() == ["ntp1.corpac.local", "ntp2.corpac.local"]


def test_una_variable_vacia_cae_en_los_valores_por_defecto(monkeypatch):
    """docker-compose define NTP_SERVERS como cadena vacía cuando no está en
    el .env. Si eso se tomara al pie de la letra quedaría una lista vacía y el
    reloj no se sincronizaría nunca, en silencio."""
    monkeypatch.setenv("NTP_SERVERS", "   ")
    assert clock._configured_servers() == [
        "time.cloudflare.com",
        "pool.ntp.org",
        "time.windows.com",
    ]


def test_el_estado_del_reloj_dice_si_esta_verificado():
    estado = clock.sync_status()
    assert set(estado) == {
        "sincronizado",
        "ultima_sincronizacion",
        "offset_segundos",
        "servidor",
        "servidores_configurados",
        "ultimo_error",
    }
    assert isinstance(estado["sincronizado"], bool)
    assert estado["servidores_configurados"]


def test_server_time_publica_el_estado_del_reloj():
    """Sin esto, un NTP inalcanzable degrada sin que nadie se entere."""
    resp = TestClient(app).get("/server-time")
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["utc"].endswith("Z")
    assert "reloj" in cuerpo
    assert "sincronizado" in cuerpo["reloj"]
