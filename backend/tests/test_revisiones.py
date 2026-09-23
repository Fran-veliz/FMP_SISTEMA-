"""Los niveles CTOT son filas, no columnas.

Eran 23 columnas (`etd1..ctot5`, `sec1..sec5`, `h_rev1..rev4`). No es una
violación formal de 1FN -- son escalares --, pero sí una colección limitada
por columnas: el quinto nivel costó una migración con ALTER TABLE
(e3f7a91c25b8) y el sexto habría costado otra.

El resto del sistema sigue viendo `etd1`, `ctot3`, `rev2`... como atributos
del vuelo: son propiedades sobre las filas. Estas pruebas fijan las dos cosas
que tienen que seguir siendo ciertas -- el traslado de los motivos y la
paridad de lo que ve la API -- y la que se gana: un nivel más es una fila más.
"""
from fastapi.testclient import TestClient

from app import database
from app.models import CAMPOS_DE_NIVEL, Flight, FlightRevision


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


NIVELES_COMPLETOS = {
    "etd1": "1200", "ctot1": "1210", "sec1": "SxSEC",
    "h_rev1": "1215", "rev1": "RxTFC",
    "etd2": "1220", "ctot2": "1230", "sec2": "SxCAP",
    "h_rev2": "1235", "rev2": "RxMET",
    "etd3": "1240", "ctot3": "1250", "sec3": "SxSEC",
    "h_rev3": "1255", "rev3": "RxTWR",
    "etd4": "1300", "ctot4": "1310", "sec4": "SxCAP",
    "h_rev4": "1315", "rev4": "RxACC",
    "etd5": "1320", "ctot5": "1330", "sec5": "SxSEC",
}


def _crear(client: TestClient, **campos) -> dict:
    resp = client.post(
        "/flights",
        json={"sector": "SUR", "flight_date": "2026-06-03", "vuelo": "LPE2026", **campos},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _niveles(vuelo_id: int) -> dict[int, FlightRevision]:
    with database.SessionLocal() as db:
        return {
            r.numero_revision: r
            for r in db.query(FlightRevision)
            .filter(FlightRevision.vuelo_gestionado_id == vuelo_id)
            .all()
        }


def test_el_motivo_de_revision_pertenece_al_nivel_que_se_abre():
    """`h_rev1/rev1` es el motivo con el que se pasa del nivel 1 al 2, así que
    viaja con el nivel 2. Es la lectura que ya hacía la grilla. El nivel 1 no
    tiene motivo de revisión: no hay nivel anterior que lo justifique."""
    client = _fresh_client()
    _login(client)
    creado = _crear(client, **NIVELES_COMPLETOS)

    niveles = _niveles(creado["id"])
    assert sorted(niveles) == [1, 2, 3, 4, 5]

    assert niveles[1].etd == "1200" and niveles[1].ctot == "1210"
    assert niveles[1].motivo_secuencia == "SxSEC"
    assert niveles[1].hora_revision is None
    assert niveles[1].motivo_revision is None

    # el motivo que cerró el nivel 1 abre el 2
    assert niveles[2].hora_revision == "1215"
    assert niveles[2].motivo_revision == "RxTFC"
    assert niveles[2].etd == "1220"

    # y el último: h_rev4/rev4 abren el nivel 5
    assert niveles[5].hora_revision == "1315"
    assert niveles[5].motivo_revision == "RxACC"
    assert niveles[5].ctot == "1330"


def test_la_api_devuelve_los_mismos_23_campos_que_antes():
    """Paridad: el contrato de la grilla no cambió al mover los datos."""
    client = _fresh_client()
    _login(client)
    creado = _crear(client, **NIVELES_COMPLETOS)

    for campo, esperado in NIVELES_COMPLETOS.items():
        assert creado[campo] == esperado, f"{campo} volvio como {creado[campo]!r}"

    # y también al releer desde la base, no solo en la respuesta del POST
    leido = next(
        f
        for f in client.get(
            "/flights", params={"sector": "SUR", "flight_date": "2026-06-03"}
        ).json()
        if f["id"] == creado["id"]
    )
    for campo, esperado in NIVELES_COMPLETOS.items():
        assert leido[campo] == esperado, f"{campo} se leyo como {leido[campo]!r}"


def test_un_nivel_incompleto_se_conserva():
    """Un nivel con ETD y sin CTOT es una solicitud sin respuesta: un estado
    real de la operación, no un error de datos."""
    client = _fresh_client()
    _login(client)
    creado = _crear(client, etd1="1200", ctot1="1210", etd2="1220")

    assert creado["etd2"] == "1220"
    assert creado["ctot2"] is None
    assert set(_niveles(creado["id"])) == {1, 2}


def test_un_vuelo_sin_niveles_no_genera_filas():
    """La grilla guarda filas nuevas con las 23 columnas en blanco: eso no
    puede dejar cinco revisiones vacías por vuelo."""
    client = _fresh_client()
    _login(client)
    creado = _crear(client, **{campo: "" for campo in CAMPOS_DE_NIVEL})

    assert _niveles(creado["id"]) == {}


def test_corregir_una_digitacion_no_crea_otra_revision():
    """Corregir el mismo nivel mantiene su fila y queda en el historial. Cinco
    correcciones de tipeo no son cinco negociaciones con la aerolínea."""
    client = _fresh_client()
    _login(client)
    creado = _crear(client, etd1="1200", ctot1="1210")

    nivel = _niveles(creado["id"])[1]
    id_original, creado_en_original = nivel.id, nivel.creado_en

    client.patch(f"/flights/{creado['id']}", json={"ctot1": "1215"})

    niveles = _niveles(creado["id"])
    assert len(niveles) == 1, "corregir un nivel no debe crear otro"
    assert niveles[1].id == id_original
    assert niveles[1].ctot == "1215"
    # la autoría del nivel es de quien lo abrió, no de quien corrigió
    assert niveles[1].creado_en == creado_en_original

    # el cambio sí quedó en el historial del vuelo
    historial = client.get(f"/flights/{creado['id']}/history").json()
    assert any(h["field_name"] == "ctot1" and h["new_value"] == "1215" for h in historial)


def test_una_revision_nueva_crea_otra_fila():
    client = _fresh_client()
    _login(client)
    creado = _crear(client, etd1="1200", ctot1="1210")

    client.patch(
        f"/flights/{creado['id']}",
        json={"h_rev1": "1215", "rev1": "RxTFC", "etd2": "1220", "ctot2": "1230"},
    )

    niveles = _niveles(creado["id"])
    assert sorted(niveles) == [1, 2]
    assert niveles[2].motivo_revision == "RxTFC"
    assert niveles[2].ctot == "1230"


def test_el_motivo_se_referencia_al_catalogo_y_el_texto_se_conserva():
    """Un código que está en el catálogo se referencia; uno histórico que no
    está conserva su texto con la referencia nula. Apuntarlo al motivo más
    parecido cambiaría el motivo aeronáutico registrado."""
    client = _fresh_client()
    _login(client)
    creado = _crear(
        client,
        etd1="1200", ctot1="1210", sec1="SxSEC",
        h_rev1="1215", rev1="NOEXISTE", etd2="1220",
    )

    niveles = _niveles(creado["id"])
    assert niveles[1].motivo_secuencia == "SxSEC"
    assert niveles[1].motivo_secuencia_id is not None, "SxSEC esta en el catalogo"

    assert niveles[2].motivo_revision == "NOEXISTE"
    assert niveles[2].motivo_revision_id is None


def test_borrar_el_vuelo_se_lleva_sus_niveles():
    client = _fresh_client()
    _login(client)
    creado = _crear(client, etd1="1200", ctot1="1210")

    assert client.delete(f"/flights/{creado['id']}", params={"reason": "prueba"}).status_code == 204
    assert _niveles(creado["id"]) == {}


def test_las_columnas_de_nivel_ya_no_estan_en_el_vuelo():
    """Las 23 columnas viejas se retiraron (migración e5c81a6b9d34) después de
    comprobar la paridad contra producción: cero diferencias en los 224.379
    vuelos. El valor vive en la fila de revisión y en ningún otro lado."""
    client = _fresh_client()
    _login(client)
    creado = _crear(client, etd1="1200", ctot1="1210")

    columnas = {c.name for c in Flight.__table__.columns}
    for plano in CAMPOS_DE_NIVEL:
        assert plano not in columnas, f"{plano} sigue siendo una columna del vuelo"

    with database.SessionLocal() as db:
        vuelo = db.get(Flight, creado["id"])
        # y sigue leyéndose igual: es una propiedad sobre las filas
        assert vuelo.etd1 == "1200"
        assert len(vuelo.revisiones) == 1


def test_se_puede_registrar_un_sexto_nivel_sin_migrar_nada():
    """Lo que se gana con el cambio. El quinto nivel costó una migración con
    ALTER TABLE; el sexto es un INSERT.

    Los atributos planos llegan hasta el quinto, así que este nivel se crea
    sobre la fila directamente -- que es exactamente el punto: la tabla ya no
    tiene techo."""
    client = _fresh_client()
    _login(client)
    creado = _crear(client, **NIVELES_COMPLETOS)

    with database.SessionLocal() as db:
        db.add(
            FlightRevision(
                vuelo_gestionado_id=creado["id"],
                numero_revision=6,
                etd="1340",
                ctot="1350",
                hora_revision="1335",
                motivo_revision="RxTFC",
            )
        )
        db.commit()

    niveles = _niveles(creado["id"])
    assert sorted(niveles) == [1, 2, 3, 4, 5, 6]
    assert niveles[6].ctot == "1350"
