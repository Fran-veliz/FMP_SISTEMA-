"""Reloj UTC corregido contra NTP: HORA, timestamps de turno, etc. no deben
depender de que el reloj del sistema (laptop/servidor donde corre Docker)
esté bien puesto o sincronizado.

Al arrancar (y cada RESYNC_SECONDS) se consulta un servidor NTP y se guarda
el offset entre ese tiempo real y el reloj local del proceso. `now_utc()`
devuelve siempre reloj-local + offset. Si el NTP no responde (sin red,
firewall, etc.) se sigue usando el reloj local sin corregir: la
sincronización es un plus, nunca un punto de falla para el resto de la app.

QUÉ SERVIDORES SE CONSULTAN
---------------------------
Se configuran con la variable de entorno NTP_SERVERS (lista separada por
comas). Los valores por defecto son servidores públicos de internet, que
sirven para una instalación de escritorio pero NO en una red interna sin
salida: ahí hay que apuntar al NTP de la propia organización, o el reloj
nunca se verifica.

POR QUÉ IMPORTA QUE LA FALLA SE VEA
-----------------------------------
Sin registro de estado, un NTP inalcanzable degradaba en silencio: la app
seguía andando con el reloj del contenedor y nadie se enteraba de que la
hora que se muestra -- que es el producto del sistema -- había dejado de
estar verificada. Por eso `sync_status()` expone si alguna vez sincronizó,
cuándo fue la última vez y con qué error falló; `/server-time` lo publica.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta

import ntplib

logger = logging.getLogger(__name__)

# Públicos por defecto; en red interna se apunta al NTP de la organización.
_DEFAULT_NTP_SERVERS = "time.cloudflare.com,pool.ntp.org,time.windows.com"


def _configured_servers() -> list[str]:
    # `.strip() or default` y no un default de os.environ.get: docker-compose
    # define NTP_SERVERS como cadena vacía cuando no está en el .env, así que
    # la variable existe pero no vale -- con get(clave, default) quedaría una
    # lista vacía y el reloj no se sincronizaría nunca.
    crudo = os.environ.get("NTP_SERVERS", "").strip() or _DEFAULT_NTP_SERVERS
    return [s.strip() for s in crudo.split(",") if s.strip()]


_NTP_SERVERS = _configured_servers()
_RESYNC_SECONDS = 30 * 60

_offset = timedelta(0)
_lock = threading.Lock()
_resync_started = False

# Estado de la sincronización, para poder diagnosticar desde afuera.
_last_sync_at: datetime | None = None
_last_error: str | None = None
_last_server: str | None = None


def _fetch_ntp_offset() -> tuple[timedelta | None, str | None, str | None]:
    """Devuelve (offset, servidor_que_respondió, último_error)."""
    client = ntplib.NTPClient()
    error: str | None = None
    for server in _NTP_SERVERS:
        try:
            response = client.request(server, version=3, timeout=3)
            # ntplib calcula el desfase con los cuatro timestamps del
            # intercambio; tx_time - inicio local incluía latencia de red.
            return timedelta(seconds=response.offset), server, None
        except Exception as exc:  # sin red, DNS, timeout, servidor caído...
            error = f"{server}: {exc}"
            logger.warning("No se pudo sincronizar con NTP %s: %s", server, exc)
    return None, None, error


def sync_offset() -> timedelta | None:
    """Sincroniza una vez contra NTP y actualiza el offset global. Devuelve
    el offset nuevo, o None si ningún servidor respondió (offset anterior
    se mantiene sin cambios)."""
    global _offset, _last_sync_at, _last_error, _last_server
    offset, server, error = _fetch_ntp_offset()
    with _lock:
        if offset is not None:
            _offset = offset
            _last_sync_at = datetime.utcnow() + offset
            _last_server = server
            _last_error = None
        else:
            _last_error = error
    if offset is not None:
        logger.info("Reloj corregido contra NTP %s: offset=%s", server, offset)
    return offset


def now_utc() -> datetime:
    with _lock:
        offset = _offset
    return datetime.utcnow() + offset


def sync_status() -> dict:
    """Estado del reloj, para diagnóstico y monitoreo.

    `sincronizado` en False no es un error de la app: significa que la hora
    que se está usando es la del contenedor, sin verificar. En una red sin
    salida a internet es lo esperable hasta que NTP_SERVERS apunte al
    servidor NTP interno."""
    with _lock:
        offset, sync_at, error, server = _offset, _last_sync_at, _last_error, _last_server
    return {
        "sincronizado": sync_at is not None,
        "ultima_sincronizacion": sync_at.isoformat() + "Z" if sync_at else None,
        "offset_segundos": round(offset.total_seconds(), 3),
        "servidor": server,
        "servidores_configurados": _NTP_SERVERS,
        "ultimo_error": error,
    }


def start_background_resync() -> None:
    """Sincroniza en un thread aparte (no bloquea el arranque de la API) y
    reintenta cada RESYNC_SECONDS para cubrir drift del reloj local con el
    tiempo. Idempotente: un solo thread por proceso, aunque `on_startup` se
    dispare varias veces (como pasa en los tests, que crean un TestClient(app)
    por caso -- sin esto, cada uno lanzaría su propio thread de resync)."""
    global _resync_started
    with _lock:
        if _resync_started:
            return
        _resync_started = True

    def loop() -> None:
        while True:
            sync_offset()
            threading.Event().wait(_RESYNC_SECONDS)

    threading.Thread(target=loop, daemon=True).start()
