"""El itinerario pertenece a un aeródromo, no al sistema.

Hasta 2026 el sistema fue mono-aeródromo: el itinerario de Lima era "el"
itinerario y nadie tenía que decir de quién era. Al entrar Cusco, esa
suposición implícita se vuelve un error silencioso -- el mismo call sign
figura el mismo día en los dos itinerarios (sale de Lima, llega a Cusco).

Estas pruebas fijan la separación en el camino de escritura, que es donde el
daño es irreversible: una carga de Cusco que borre el itinerario de Lima o que
cancele vuelos de la grilla de Lima no se deshace.
"""
from fastapi.testclient import TestClient

from app import database
from app.models import Importacion, ItineraryEntry, Sector
from app.services import itinerario


def _fresh_client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _login(client: TestClient) -> dict:
    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "1234"},
    )
    assert resp.status_code == 200, resp.text
    shift = resp.json()
    client.headers["X-Shift-Token"] = shift["session_token"]
    return shift


# Un itinerario mínimo válido, para las pruebas a las que no les importa el
# contenido del archivo sino a qué aeródromo va a parar.
_UNA_FILA = "call_sign,direction,hora_utc,aerodromo\nLPE1,ARR,1200,SPIM\n"


def _upload_csv(client: TestClient, effective_from: str, rows: str, **params):
    csv_content = "call_sign,direction,hora_utc,aerodromo\n" + rows
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": effective_from, "confirmado": True, **params},
        files={"file": ("itin.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_cada_sector_sabe_de_que_aerodromo_es():
    """Las dos direcciones de la relación tienen que coincidir.

    `estacion_de_sector` alimenta el cruce con el itinerario y
    `sectores_de_estacion` decide a qué vuelos alcanza una cancelación. Si
    dijeran cosas distintas, un vuelo podría resolver su slot contra un
    aeródromo y quedar fuera del alcance de las cancelaciones de ese mismo
    aeródromo.
    """
    for sector in Sector:
        estacion = itinerario.estacion_de_sector(sector)
        assert sector in itinerario.sectores_de_estacion(estacion)

    assert itinerario.sectores_de_estacion("SPZO") == []


def test_la_carga_deja_registrado_de_que_aerodromo_es_el_itinerario():
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1545,SPZO\n")

    db = database.SessionLocal()
    try:
        assert [e.estacion for e in db.query(ItineraryEntry).all()] == ["SPJC"]
        assert [u.estacion for u in db.query(Importacion).all()] == ["SPJC"]
    finally:
        db.close()

    # y el historial que ve el operador también lo dice
    assert client.get("/itinerary/uploads").json()[0]["estacion"] == "SPJC"


def test_cargar_el_itinerario_de_cusco_no_borra_el_de_lima():
    """El caso irreversible: `alcance="desde"` borra el tramo antes de
    escribir. Sin acotar por estación, la carga de un aeródromo se llevaba
    puesto el itinerario del otro de esa fecha en adelante."""
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1545,SPIM\n")
    _upload_csv(client, "2026-06-03", "LPE9999,ARR,0800,SPIM\n", estacion="SPZO")

    lima = client.get(
        "/itinerary/lookup",
        params={"flight_date": "2026-06-03", "call_sign": "LPE2026", "estacion": "SPJC"},
    ).json()
    assert lima["found"] is True, "la carga de Cusco borró el itinerario de Lima"

    cusco = client.get(
        "/itinerary/lookup",
        params={"flight_date": "2026-06-03", "call_sign": "LPE9999", "estacion": "SPZO"},
    ).json()
    assert cusco["found"] is True

    # y cada uno figura solo en el suyo
    assert client.get(
        "/itinerary/lookup",
        params={"flight_date": "2026-06-03", "call_sign": "LPE9999", "estacion": "SPJC"},
    ).json()["found"] is False


def test_cargar_el_itinerario_de_cusco_no_cancela_vuelos_de_lima():
    """La grilla es de Lima. Un vuelo que no figura en el itinerario de Cusco
    no es un vuelo cancelado: es un vuelo de otro aeródromo."""
    client = _fresh_client()
    _login(client)
    _upload_csv(client, "2026-06-03", "LPE2026,ARR,1545,SPIM\n")
    vuelo = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026"},
    )
    assert vuelo.status_code == 200, vuelo.text

    _upload_csv(client, "2026-06-03", "LPE9999,ARR,0800,SPIM\n", estacion="SPZO")

    grilla = client.get("/flights", params={"sector": "SUR", "flight_date": "2026-06-03"}).json()
    assert [f["vuelo"] for f in grilla] == ["LPE2026"]
    assert grilla[0]["cancelado"] is False, "una carga de Cusco canceló un vuelo de Lima"


def test_no_se_puede_cargar_a_un_aerodromo_que_no_existe():
    """`estacion` decide qué filas borra la carga. Un código mal escrito no
    puede pasar en silencio: cargaría el itinerario en un aeródromo fantasma
    y borraría el tramo de nadie."""
    client = _fresh_client()
    _login(client)
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True, "estacion": "XXXX"},
        files={"file": ("itin.csv", "call_sign,direction,hora_utc,aerodromo\nLPE1,ARR,1200,SPIM\n", "text/csv")},
    )
    assert resp.status_code == 404
    assert "XXXX" in resp.json()["detail"]
    assert client.get("/itinerary/uploads").json() == []


def test_el_catalogo_dice_a_que_aerodromos_se_puede_cargar():
    """Lo que lista `/estaciones` tiene que ser lo que la carga acepta.

    El desplegable de la pantalla se arma con esta respuesta. Si ofreciera un
    aeródromo que `_validar_estacion` rechaza, el operador elegiría una opción
    válida a la vista y se llevaría un 404 sin entender por qué; y si dejara
    fuera uno que sí se puede cargar, no habría forma de llegar a él desde la
    pantalla.
    """
    client = _fresh_client()
    _login(client)

    catalogo = client.get("/estaciones").json()
    codigos = [e["codigo_oaci"] for e in catalogo]
    assert codigos == sorted(codigos), "el desplegable saldría desordenado"
    assert "SPJC" in codigos

    for codigo in codigos:
        resp = client.post(
            "/itinerary/preview",
            params={"effective_from": "2026-06-03", "estacion": codigo},
            files={"file": ("itin.csv", _UNA_FILA, "text/csv")},
        )
        assert resp.status_code == 200, (codigo, resp.text)
        # Y la vista previa devuelve de qué aeródromo habla: es lo que se
        # confirma al aplicar, no lo que el formulario crea tener elegido.
        assert resp.json()["estacion"] == codigo


def test_el_catalogo_no_ofrece_aerodromos_dados_de_baja():
    """Un aeródromo inactivo no se puede cargar, así que tampoco se ofrece."""
    from sqlalchemy import select

    from app.models import Aerodromo

    db = database.SessionLocal()
    try:
        # SPQU ya está en el maestro: entra como extremo de ruta por su
        # equivalencia IATA (AQP). Se lo habilita como estación y se lo da de
        # baja, sobre la MISMA fila.
        #
        # Con las dos tablas separadas esto se cargaba dos veces sin que nada
        # lo impidiera -- una en `aeropuerto` y otra en `codigo_aeropuerto` --,
        # y no había forma de saber que eran el mismo aeródromo.
        arequipa = db.execute(
            select(Aerodromo).where(Aerodromo.codigo_oaci == "SPQU")
        ).scalars().one()
        arequipa.nombre_operativo = "AREQUIPA"
        arequipa.es_estacion_ctot = True
        arequipa.activo = False
        db.commit()
    finally:
        db.close()

    client = _fresh_client()
    _login(client)
    assert "SPQU" not in [e["codigo_oaci"] for e in client.get("/estaciones").json()]

    # y la carga tampoco lo acepta: es la misma condición
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True, "estacion": "SPQU"},
        files={"file": ("itin.csv", _UNA_FILA, "text/csv")},
    )
    assert resp.status_code == 404


def test_un_aerodromo_del_catalogo_general_no_es_una_estacion():
    """Estar en el maestro no convierte a un aeródromo en estación CTOT.

    El maestro cubre la unión: las estaciones con itinerario propio y también
    los extremos de rutas, incluidos los extranjeros, que antes vivían en la
    tabla de equivalencias IATA. Si el desplegable ofreciera todo, el operador
    podría intentar cargarle un itinerario a Ámsterdam."""
    client = _fresh_client()
    _login(client)

    ofrecidos = [e["codigo_oaci"] for e in client.get("/estaciones").json()]
    assert "SPJC" in ofrecidos

    # EHAM entra al maestro por la equivalencia IATA (AMS), no como estación
    todas = client.get("/airports").json()
    assert "EHAM" in [a["icao"] for a in todas]
    assert "EHAM" not in ofrecidos

