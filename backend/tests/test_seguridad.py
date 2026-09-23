"""Refuerzos de seguridad de la revisión del 2026-07-31.

Cubre los tres puntos que se arreglaron del lado nuestro (los otros -- HTTPS,
/docs, CORS, usuario del contenedor -- son de despliegue):

  2. El token de turno deja de valer tras un tiempo sin usarse. Antes vivía
     para siempre si nadie cerraba turno.
  3. El token del WebSocket ya no viaja en la URL.
  6. El tamaño del archivo se valida ANTES de leerlo entero.
"""
import io
import hashlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import clock, database
from app.auth import SHIFT_IDLE_TIMEOUT_HOURS
from app.main import app
from app.models import ShiftLog
from app.routers.flight_history import MAX_HISTORY_UPLOAD_BYTES
from app.routers.flight_socket import WS_TOKEN_PROTOCOL


def _client() -> TestClient:
    return TestClient(app)


def test_pin_nuevo_se_guarda_con_argon2():
    db = database.SessionLocal()
    try:
        from app.models import Controller

        controller = db.query(Controller).filter(Controller.usuario == "SPADILLA").one()
        assert controller.pin_hash.startswith("$argon2id$")
        assert "1234" not in controller.pin_hash
    finally:
        db.close()


def test_pin_sha256_existente_migra_a_argon2_al_ingresar():
    from app.models import Controller

    db = database.SessionLocal()
    try:
        controller = db.query(Controller).filter(Controller.usuario == "SPADILLA").one()
        controller.pin_hash = hashlib.sha256((controller.pin_salt + "1234").encode()).hexdigest()
        db.commit()
    finally:
        db.close()

    response = _client().post(
        "/shifts/clock-in",
        json={"operator_name": "SPADILLA", "position": "FMP SUR", "pin": "1234"},
    )
    assert response.status_code == 200

    db = database.SessionLocal()
    try:
        controller = db.query(Controller).filter(Controller.usuario == "SPADILLA").one()
        assert controller.pin_hash.startswith("$argon2id$")
    finally:
        db.close()


def test_documentacion_se_desactiva_en_produccion(monkeypatch):
    import importlib
    import app.main as main_module

    monkeypatch.setenv("ENVIRONMENT", "production")
    production_main = importlib.reload(main_module)
    assert production_main.app.docs_url is None
    assert production_main.app.redoc_url is None
    assert production_main.app.openapi_url is None

    monkeypatch.setenv("ENVIRONMENT", "test")
    importlib.reload(main_module)


def test_documentacion_falla_cerrada_con_un_valor_desconocido(monkeypatch):
    """Un typo en ENVIRONMENT no debe publicar el esquema de la API.

    Antes se comparaba contra "production": cualquier otro valor -- "produccion"
    mal escrito, o la variable sin definir en el servidor -- dejaba /docs
    abierto. Ahora solo lo abren "development" y "test"."""
    import importlib
    import app.main as main_module

    for valor in ("produccion", "PROD", "prod", ""):
        monkeypatch.setenv("ENVIRONMENT", valor)
        recargado = importlib.reload(main_module)
        assert recargado.app.docs_url is None, valor
        assert recargado.app.openapi_url is None, valor

    monkeypatch.setenv("ENVIRONMENT", "test")
    importlib.reload(main_module)


def test_la_credencial_inicial_tiene_que_ser_tipeable(monkeypatch):
    """No alcanza con que sea larga: tiene que poder escribirse en la pantalla.

    El marcador de posición que traía .env.example ("cambiar-pin-operadores")
    pasaba el largo mínimo, así que copiar ese archivo sin editarlo arrancaba
    el sistema, creaba los veinte perfiles y dejaba a TODOS afuera: el campo de
    ingreso acepta solo dígitos y hasta ocho. Fallaba en silencio, con el
    síntoma más desconcertante posible -- "PIN incorrecto" para todo el mundo.
    """
    from app.seed_data import _initial_pin

    monkeypatch.setenv("ENVIRONMENT", "production")

    for valor, motivo in [
        ("cambiar-pin-operadores", "no es numérica"),
        ("mi-clave-1", "tiene letras"),
        ("1234", "menos de 6 dígitos"),
        ("123456789", "más de 8 dígitos"),
        ("", "vacía"),
    ]:
        monkeypatch.setenv("PIN_DE_PRUEBA", valor)
        with pytest.raises(RuntimeError):
            _initial_pin("PIN_DE_PRUEBA")

    # Lo que sí acepta el formulario: entre 6 y 8 dígitos.
    for valor in ("481907", "12345678"):
        monkeypatch.setenv("PIN_DE_PRUEBA", valor)
        assert _initial_pin("PIN_DE_PRUEBA") == valor

    monkeypatch.setenv("ENVIRONMENT", "test")


def test_el_freno_por_origen_corta_la_fuerza_bruta():
    """El bloqueo por perfil cuida una cuenta; sin este freno, un script puede
    recorrer la nómina entera en paralelo y hacer que la API gaste un hash
    Argon2 por intento."""
    from app import auth

    auth._intentos_por_origen.clear()
    client = _client()
    # Nombres inexistentes: así no interfiere el bloqueo por perfil y se mide
    # únicamente el freno por origen.
    codigos = [
        client.post(
            "/shifts/clock-in",
            json={"operator_name": f"NOEXISTE{i}", "position": "FMP SUR", "pin": "0000"},
        ).status_code
        for i in range(auth.INTENTOS_MAX_POR_ORIGEN + 2)
    ]
    assert codigos[: auth.INTENTOS_MAX_POR_ORIGEN] == [401] * auth.INTENTOS_MAX_POR_ORIGEN
    assert codigos[auth.INTENTOS_MAX_POR_ORIGEN] == 429
    auth._intentos_por_origen.clear()


def test_un_ingreso_correcto_libera_el_freno_del_origen():
    from app import auth

    auth._intentos_por_origen.clear()
    client = _client()
    for i in range(10):
        client.post(
            "/shifts/clock-in",
            json={"operator_name": f"NOEXISTE{i}", "position": "FMP SUR", "pin": "0000"},
        )
    assert auth._intentos_por_origen  # quedó registro del origen
    resp = client.post(
        "/shifts/clock-in", json={"operator_name": "MROMERO", "position": "FMP SUR", "pin": "1234"}
    )
    assert resp.status_code == 200
    assert not auth._intentos_por_origen  # el ingreso correcto lo limpia


def test_el_turno_se_firma_con_el_nombre_de_la_nomina():
    """Se entra tipeando en minúscula; el turno -- y con él todo el historial
    de vuelos que firme -- debe quedar con el nombre canónico del perfil."""
    from app import auth

    auth._intentos_por_origen.clear()
    resp = _client().post(
        "/shifts/clock-in", json={"operator_name": "  gestepia ", "position": "FMP NOR", "pin": "1234"}
    )
    assert resp.status_code == 200
    assert resp.json()["operator_name"] == "GESTEPIA"


def test_el_pin_tiene_largo_acotado():
    """Sin tope, un 'PIN' de megabytes llegaba igual hasta Argon2."""
    from app import auth

    auth._intentos_por_origen.clear()
    client = _client()
    assert client.post(
        "/shifts/clock-in", json={"operator_name": "MROMERO", "position": "FMP SUR", "pin": "12"}
    ).status_code == 422
    assert client.post(
        "/shifts/clock-in", json={"operator_name": "MROMERO", "position": "FMP SUR", "pin": "9" * 5000}
    ).status_code == 422


def test_el_limite_de_anio_se_aplica_en_todos_los_endpoints_con_fecha():
    """El límite de año de la DGAC vivía SOLO en /flights y en la exportación.

    Una revisión externa lo destapó: un perfil DGAC2 pedía /forecast o
    /itinerary/lookup con una fecha de 2021 y recibía 200. La regla existía,
    pero se aplicaba en un endpoint de cinco."""
    from app import auth

    auth._intentos_por_origen.clear()
    client = _client()
    token = _login(client, name="DGAC2", pin="2581")["session_token"]
    h = {"X-Shift-Token": token}
    fuera_de_anio = "2021-06-01"

    for ruta, params in [
        ("/flights", {"sector": "SUR", "flight_date": fuera_de_anio}),
        ("/forecast", {"flight_date": fuera_de_anio}),
        ("/itinerary/lookup", {"flight_date": fuera_de_anio, "call_sign": "LPE2000"}),
        ("/flights/export", {"flight_date": fuera_de_anio}),
    ]:
        assert client.get(ruta, params=params, headers=h).status_code == 403, ruta


def test_la_grilla_solo_se_corrige_hasta_un_dia_hacia_atras():
    """La regla vivía solo en el frontend: una llamada directa a la API creaba,
    editaba y borraba vuelos de 2021. Comprobado antes de corregirlo."""
    from app import auth, clock

    auth._intentos_por_origen.clear()
    client = _client()
    token = _login(client)["session_token"]
    h = {"X-Shift-Token": token}
    hoy = clock.now_utc().date()
    ayer = hoy - timedelta(days=1)
    anteayer = hoy - timedelta(days=2)

    def crear(fecha, vuelo):
        return client.post(
            "/flights",
            json={"sector": "SUR", "flight_date": fecha.isoformat(), "vuelo": vuelo, "eta_aircon": "1200"},
            headers=h,
        )

    assert crear(hoy, "LPE8001").status_code == 200          # la jornada en curso
    assert crear(ayer, "LPE8002").status_code == 200         # un día hacia atrás
    assert crear(hoy + timedelta(days=1), "LPE8003").status_code == 200  # el futuro no se limita
    assert crear(anteayer, "LPE8004").status_code == 403     # historia cerrada

    # Editar y borrar responden a la misma regla, no solo crear.
    vuelo_de_ayer = crear(ayer, "LPE8005").json()["id"]
    assert client.patch(f"/flights/{vuelo_de_ayer}", json={"observaciones": "ok"}, headers=h).status_code == 200

    from app.models import Flight

    db = database.SessionLocal()
    try:
        viejo = Flight(
            sector="SUR", flight_date=anteayer, numero_fila=999, vuelo="LPE8006",
            hora="1200", updated_at=clock.now_utc(),
        )
        db.add(viejo)
        db.commit()
        viejo_id = viejo.id
    finally:
        db.close()
    assert client.patch(f"/flights/{viejo_id}", json={"observaciones": "no"}, headers=h).status_code == 403
    assert client.delete(f"/flights/{viejo_id}", headers=h).status_code == 403


def test_cualquier_operador_puede_trabajar_cualquier_sector():
    """Esto NO es un descuido: es la regla que definió el área.

    Una revisión externa lo marcó como fallo de autorización porque el backend
    no comprueba que el sector del vuelo coincida con la posición del turno.
    Pero en una FMP de dos posiciones la cobertura es rutina: un operador cubre
    la otra zona cuando queda solo. Restringirlo en el backend romperia esa
    operación. El control no es impedirlo sino dejar registro de quién lo hizo,
    y eso ya está: cada cambio queda firmado con el operador del turno.

    La prueba fija la decisión para que la próxima revisión no la marque otra vez.
    """
    from app import auth, clock

    auth._intentos_por_origen.clear()
    client = _client()
    token = _login(client)["session_token"]  # turno en FMP SUR
    h = {"X-Shift-Token": token}
    hoy = clock.now_utc().date().isoformat()

    resp = client.post(
        "/flights",
        json={"sector": "NOR", "flight_date": hoy, "vuelo": "LPE8100", "eta_aircon": "1300"},
        headers=h,
    )
    assert resp.status_code == 200
    assert client.patch(f"/flights/{resp.json()['id']}", json={"observaciones": "cobertura"}, headers=h).status_code == 200


def test_el_websocket_se_cierra_cuando_el_turno_deja_de_valer(monkeypatch):
    """El canal en vivo autenticaba una sola vez, al abrirse.

    Quien cerraba turno y dejaba la pestaña abierta seguía recibiendo toda la
    operación de la FMP en vivo, sin turno detrás. Nadie tenía que hacer nada
    raro: alcanzaba con dejar la pantalla prendida.
    """
    from starlette.websockets import WebSocketDisconnect

    from app import auth
    from app.routers import flight_socket
    from app.routers.flight_socket import WS_TOKEN_PROTOCOL

    # El control real corre cada 30 s; acá se acelera para no esperar.
    monkeypatch.setattr(flight_socket, "SEGUNDOS_REVALIDACION_WS", 0.05)

    auth._intentos_por_origen.clear()
    client = _client()
    turno = _login(client)
    token, turno_id = turno["session_token"], turno["id"]

    with client.websocket_connect("/ws/SUR", subprotocols=[WS_TOKEN_PROTOCOL, token]) as ws:
        # Con el turno abierto, el canal recibe lo que pasa.
        client.post(
            "/flights",
            json={"sector": "SUR", "flight_date": clock.now_utc().date().isoformat(),
                  "vuelo": "LPE7001", "eta_aircon": "1200"},
            headers={"X-Shift-Token": token},
        )
        assert ws.receive_json()["type"] == "flight_upserted"

        # Se cierra el turno: el canal tiene que cortarse solo.
        client.post(f"/shifts/{turno_id}/clock-out", headers={"X-Shift-Token": token})

        with pytest.raises(WebSocketDisconnect):
            # Puede llegar todavía algún aviso del propio cierre antes del corte.
            for _ in range(20):
                ws.receive_json()


def test_la_base_impide_dos_turnos_abiertos_del_mismo_operador():
    """La regla vivía solo en el código: clock_in consultaba y después creaba.

    Entre esos dos pasos hay un instante. Acá se simula la carrera saltándose
    la consulta -- se inserta el segundo turno directo contra la base, que es
    exactamente lo que lograría una petición simultánea -- y la base tiene que
    rechazarlo.
    """
    from sqlalchemy.exc import IntegrityError

    from app import auth, clock
    from app.models import ShiftLog

    auth._intentos_por_origen.clear()
    client = _client()
    primero = _login(client)

    db = database.SessionLocal()
    try:
        db.add(ShiftLog(
            operator_name=primero["operator_name"],
            position="FMP NOR",
            start_time=clock.now_utc(),
            session_token="otro-token-distinto",
            last_seen_at=clock.now_utc(),
        ))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()

    # Cerrado el primero, la misma persona puede abrir otro: el índice solo
    # alcanza a los turnos sin cerrar.
    client.post(f"/shifts/{primero['id']}/clock-out",
                headers={"X-Shift-Token": primero["session_token"]})
    auth._intentos_por_origen.clear()
    assert _login(client)["id"] != primero["id"]


def test_el_pase_de_sesion_no_queda_guardado_en_la_base():
    """El PIN estaba protegido con Argon2, pero el pase se guardaba tal cual.

    Quien leyera la base con turnos abiertos podía copiar esos pases y actuar
    como esos operadores en el acto, sin conocer ningún PIN.
    """
    from app import auth
    from app.models import ShiftLog

    auth._intentos_por_origen.clear()
    client = _client()
    turno = _login(client)
    pase = turno["session_token"]

    db = database.SessionLocal()
    try:
        guardado = db.get(ShiftLog, turno["id"]).session_token
    finally:
        db.close()

    # Lo guardado NO sirve para entrar, y no se parece al pase entregado.
    assert guardado != pase
    assert pase not in guardado
    assert guardado == auth.huella_token(pase)

    # El pase real sigue funcionando...
    assert client.get("/shifts/active", headers={"X-Shift-Token": pase}).status_code == 200
    # ...y lo que hay en la base, no.
    assert client.get("/shifts/active", headers={"X-Shift-Token": guardado}).status_code == 401


def test_los_turnos_vencidos_se_cierran_y_no_quedan_activos_para_siempre():
    """El vencimiento por inactividad invalidaba el pase pero NO cerraba el turno.

    end_time quedaba en NULL indefinidamente y la bitácora seguía mostrando a
    esa persona trabajando. Cada olvido sumaba uno más, para siempre. En la
    base real había tres turnos así, de hasta 49 horas.
    """
    from app import auth, clock
    from app.models import ShiftLog

    auth._intentos_por_origen.clear()
    client = _client()
    activo = _login(client)

    # Un turno abandonado hace mucho más que el plazo de inactividad.
    inicio = clock.now_utc() - timedelta(hours=60)
    ultima_actividad = inicio + timedelta(hours=2)  # trabajó 2 h y se fue
    db = database.SessionLocal()
    try:
        olvidado = ShiftLog(
            operator_name="MROMERO", position="FMP NOR",
            start_time=inicio, last_seen_at=ultima_actividad,
            session_token="pase-viejo",
        )
        db.add(olvidado)
        db.commit()
        olvidado_id = olvidado.id
    finally:
        db.close()

    # Mirar la bitácora los cierra.
    activos = client.get("/shifts/active", headers={"X-Shift-Token": activo["session_token"]}).json()
    assert [t["operator_name"] for t in activos] == [activo["operator_name"]]

    db = database.SessionLocal()
    try:
        cerrado = db.get(ShiftLog, olvidado_id)
        assert cerrado.end_time is not None
        assert cerrado.cerrado_automaticamente is True
        assert cerrado.session_token is None
        # La duración es la trabajada de verdad (2 h), no las 60 que estuvo
        # colgado: si no, la bitácora reportaría trabajo que nadie hizo.
        #
        # Ya no es una columna: se calcula de iniciado_en y finalizado_en. El
        # cierre automático fija `end_time` en la última actividad real y no en
        # el instante del cierre, que es precisamente lo que hace que el
        # derivado siga dando las 2 h trabajadas.
        trabajados = (cerrado.end_time - cerrado.start_time).total_seconds() / 60
        assert 119 <= trabajados <= 121
        # Este turno se armó a mano, sin pasar por clock-in: es la forma que
        # tienen los turnos que ya estaban abiertos antes de `ctot.sesion`. El
        # cierre automático tiene que alcanzarlos igual, y su pase legado queda
        # invalidado por la columna (comprobado arriba), no por una sesión que
        # nunca existió.
        assert cerrado.sesiones == []
    finally:
        db.close()


def test_los_catalogos_ya_no_son_publicos():
    """Aeródromos, motivos y el historial de importaciones se leían sin turno.
    El último además filtraba nombres de archivo, quién subió y cuándo."""
    from app import auth

    auth._intentos_por_origen.clear()
    client = _client()
    rutas = ["/airports", "/reason-codes", "/flights/import-history/uploads"]
    for ruta in rutas:
        assert client.get(ruta).status_code == 401, ruta
    token = _login(client)["session_token"]
    for ruta in rutas:
        assert client.get(ruta, headers={"X-Shift-Token": token}).status_code == 200, ruta


def _login(client: TestClient, name: str = "SPADILLA", pin: str = "1234") -> dict:
    resp = client.post(
        "/shifts/clock-in", json={"operator_name": name, "position": "FMP SUR", "pin": pin}
    )
    assert resp.status_code == 200, resp.text
    shift = resp.json()
    client.headers["X-Shift-Token"] = shift["session_token"]
    return shift


def _envejecer(shift_id: int, horas: float) -> None:
    """Simula que el turno lleva ese tiempo sin actividad."""
    db = database.SessionLocal()
    try:
        fila = db.get(ShiftLog, shift_id)
        fila.last_seen_at = clock.now_utc() - timedelta(hours=horas)
        db.commit()
    finally:
        db.close()


# ------------------------------------------- 2. expiración por inactividad


def test_el_token_deja_de_valer_tras_la_inactividad():
    client = _client()
    shift = _login(client)
    hoy = clock.now_utc().date().isoformat()
    assert client.get("/flights", params={"sector": "SUR", "flight_date": hoy}).status_code == 200

    _envejecer(shift["id"], SHIFT_IDLE_TIMEOUT_HOURS + 1)

    resp = client.get("/flights", params={"sector": "SUR", "flight_date": hoy})
    assert resp.status_code == 401
    assert "inactivo" in resp.json()["detail"].lower()


def test_un_turno_activo_no_expira():
    """La cuenta corre desde la última petición, no desde que abrió el turno:
    un turno largo pero en uso no se corta solo."""
    client = _client()
    shift = _login(client)
    hoy = clock.now_utc().date().isoformat()

    # Lleva mucho abierto, pero se usó recién.
    db = database.SessionLocal()
    try:
        fila = db.get(ShiftLog, shift["id"])
        fila.start_time = clock.now_utc() - timedelta(days=3)
        fila.last_seen_at = clock.now_utc()
        db.commit()
    finally:
        db.close()

    assert client.get("/flights", params={"sector": "SUR", "flight_date": hoy}).status_code == 200


def test_la_actividad_renueva_el_plazo():
    client = _client()
    shift = _login(client)
    hoy = clock.now_utc().date().isoformat()

    # Justo por debajo del límite: la petición pasa y además refresca el
    # contador, así que el turno vuelve a tener el plazo completo.
    _envejecer(shift["id"], SHIFT_IDLE_TIMEOUT_HOURS - 0.5)
    assert client.get("/flights", params={"sector": "SUR", "flight_date": hoy}).status_code == 200

    _envejecer(shift["id"], SHIFT_IDLE_TIMEOUT_HOURS - 0.5)
    assert client.get("/flights", params={"sector": "SUR", "flight_date": hoy}).status_code == 200


def test_reenganchar_un_turno_vencido_emite_un_token_nuevo():
    """El token viejo pudo quedar guardado en otra computadora: al reanudar
    se reemplaza, no se revive."""
    client = _client()
    shift = _login(client)
    viejo = shift["session_token"]
    _envejecer(shift["id"], SHIFT_IDLE_TIMEOUT_HOURS + 1)

    resp = client.post(
        "/shifts/clock-in", json={"operator_name": "SPADILLA", "position": "FMP SUR", "pin": "1234"}
    )
    assert resp.status_code == 200
    nuevo = resp.json()["session_token"]
    assert nuevo != viejo, "se reutilizó el token vencido"

    # El viejo no sirve más
    client.headers["X-Shift-Token"] = viejo
    hoy = clock.now_utc().date().isoformat()
    assert client.get("/flights", params={"sector": "SUR", "flight_date": hoy}).status_code == 401


# ------------------------------------------- 3. token fuera de la URL del WS


def test_el_websocket_no_acepta_el_token_por_la_url():
    """Regresión: antes bastaba con ?token=... , y las URLs quedan en logs."""
    client = _client()
    shift = _login(client)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/SUR?token={shift['session_token']}"):
            pass


def test_el_websocket_acepta_el_token_por_subprotocolo():
    client = _client()
    shift = _login(client)
    with client.websocket_connect(
        "/ws/SUR", subprotocols=[WS_TOKEN_PROTOCOL, shift["session_token"]]
    ) as ws:
        assert ws is not None


def test_el_websocket_rechaza_un_token_invalido():
    client = _client()
    _login(client)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/SUR", subprotocols=[WS_TOKEN_PROTOCOL, "no-existe"]):
            pass


# --------------------------------------- 6. tamaño validado antes de leer


def test_rechaza_un_archivo_mas_grande_que_el_limite():
    client = _client()
    _login(client)
    gordo = b"x" * (MAX_HISTORY_UPLOAD_BYTES + 1024)
    resp = client.post(
        "/flights/import-history/preview",
        params={"sector": "SUR", "flight_date": clock.now_utc().date().isoformat()},
        files={"file": ("gordo.csv", io.BytesIO(gordo), "text/csv")},
    )
    assert resp.status_code == 413
    assert "grande" in resp.json()["detail"]


def test_la_lectura_acotada_corta_antes_de_terminar_el_archivo():
    """Lo que importa no es solo el 413, sino no haber leído todo primero."""
    import asyncio

    from app.uploads import read_upload_limited

    class ArchivoEnorme:
        """Simula una subida infinita: si el límite no se aplicara mientras se
        lee, esto no terminaría nunca."""

        def __init__(self) -> None:
            self.bloques_leidos = 0

        async def read(self, size: int) -> bytes:
            self.bloques_leidos += 1
            assert self.bloques_leidos < 10_000, "leyó el archivo entero antes de rechazarlo"
            return b"x" * size

    falso = ArchivoEnorme()
    with pytest.raises(Exception):
        asyncio.run(read_upload_limited(falso, 1024 * 1024))
    # 1 MB en bloques de 64 KB: alcanza con ~17 lecturas para saber que se pasa
    assert falso.bloques_leidos < 30, f"leyó {falso.bloques_leidos} bloques de más"


def test_un_perfil_desactivado_no_puede_iniciar_turno():
    """Una persona que deja la FMP se desactiva, no se borra: sus turnos,
    cambios y cargas siguen en la bitácora y tienen que poder atribuirse.

    El rechazo usa el mismo mensaje que un nombre inexistente: responder
    distinto delataría qué cuentas existen."""
    from app import database
    from app.models import Controller

    client = _client()

    # con el perfil activo entra normalmente
    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": "SPADILLA", "position": "FMP SUR", "pin": "1234"},
    )
    assert resp.status_code == 200, resp.text
    client.headers["X-Shift-Token"] = resp.json()["session_token"]
    client.post(f"/shifts/{resp.json()['id']}/clock-out")
    client.headers.pop("X-Shift-Token", None)

    with database.SessionLocal() as db:
        controller = db.query(Controller).filter(Controller.usuario == "SPADILLA").one()
        controller.activo = False
        db.commit()
        controller_id = controller.id

    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": "SPADILLA", "position": "FMP SUR", "pin": "1234"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Nombre o PIN incorrecto"

    # la fila sigue ahí: no se borró a nadie
    with database.SessionLocal() as db:
        assert db.get(Controller, controller_id) is not None


def test_el_nombre_real_es_opcional_y_no_se_inventa():
    """`nombre_completo` queda nulo hasta que alguien lo complete a mano. No se
    puede deducir el nombre real de una persona de su usuario, y rellenarlo con
    el usuario haría pasar por nombre real algo que no lo es."""
    from app import database
    from app.models import Controller

    with database.SessionLocal() as db:
        controller = db.query(Controller).filter(Controller.usuario == "SPADILLA").one()
        assert controller.nombre_completo is None

        # se completa sin tocar el acceso
        controller.nombre_completo = "Sandra Padilla"
        db.commit()

    client = _client()
    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": "SPADILLA", "position": "FMP SUR", "pin": "1234"},
    )
    assert resp.status_code == 200, resp.text
