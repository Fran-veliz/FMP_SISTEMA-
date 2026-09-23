"""Lo cargado tiene que poder decir de qué carga vino.

Había dos registros de carga casi idénticos -- `cargas.lote_carga` para el
itinerario e `ctot.importacion_historica` para las grillas cerradas -- y
ninguno estaba referenciado por lo que cargaba. La procedencia se deducía por
coincidencia de sector y fecha, que es lo que hace `borrar_historicos` a falta
de un identificador al que agarrarse.

Ahora es una sola tabla, `integracion.importacion`, y cada movimiento y cada
vuelo importado apunta a su ejecución y a su renglón de origen.
"""
from fastapi.testclient import TestClient

from app import database
from app.models import Flight, Importacion, ItineraryEntry, TipoImportacion


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


ITINERARIO = (
    "call_sign,direction,hora_utc,aerodromo\n"
    "LPE2026,ARR,1545,SPZO\n"
    "LPE2027,DEP,1700,SPZO\n"
)


def test_cada_movimiento_apunta_a_su_carga_y_a_su_fila():
    client = _fresh_client()
    _login(client)
    resp = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("itin.csv", ITINERARIO, "text/csv")},
    )
    assert resp.status_code == 200, resp.text

    with database.SessionLocal() as db:
        carga = db.query(Importacion).one()
        assert carga.tipo == TipoImportacion.ITINERARIO
        assert carga.estacion == "SPJC"
        assert carga.nombre_archivo == "itin.csv"
        assert carga.filas_aceptadas == 2

        movimientos = db.query(ItineraryEntry).all()
        assert len(movimientos) == 2
        assert all(m.importacion_id == carga.id for m in movimientos)
        # cada fila sabe de qué renglón del archivo salió
        assert {m.fila_origen for m in movimientos} == {2, 3}


def test_deshacer_una_carga_es_un_delete_por_identificador():
    """Lo que la referencia habilita: antes había que borrar por coincidencia
    de estación y fecha, que se llevaba también lo que hubiera cargado otra
    ejecución del mismo tramo."""
    client = _fresh_client()
    _login(client)
    client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("itin.csv", ITINERARIO, "text/csv")},
    )

    with database.SessionLocal() as db:
        carga_id = db.query(Importacion).one().id
        borradas = (
            db.query(ItineraryEntry)
            .filter(ItineraryEntry.importacion_id == carga_id)
            .delete()
        )
        db.commit()
        assert borradas == 2
        assert db.query(ItineraryEntry).count() == 0


def test_las_dos_clases_de_carga_conviven_en_la_misma_tabla():
    """Eran dos tablas casi iguales. Ahora se distinguen por `tipo`, y cada
    una conserva sus campos propios."""
    client = _fresh_client()
    _login(client)

    client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("itin.csv", ITINERARIO, "text/csv")},
    )

    with database.SessionLocal() as db:
        cargas = {c.tipo: c for c in db.query(Importacion).all()}
        itinerario = cargas[TipoImportacion.ITINERARIO]
        # campos propios del itinerario, presentes
        assert itinerario.vigente_desde is not None
        assert itinerario.alcance == "desde"
        # campos propios de la grilla histórica, ausentes
        assert itinerario.sector is None
        assert itinerario.fecha_operacion is None


def test_el_historial_que_ve_el_operador_no_cambio_de_forma():
    """El contrato de la API se conservó: la interfaz sigue leyendo
    `uploaded_at`, `uploaded_by`, `filename` y `effective_from` aunque la tabla
    los llame de otra manera."""
    client = _fresh_client()
    _login(client)
    client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "confirmado": True},
        files={"file": ("W25.csv", ITINERARIO, "text/csv")},
    )

    fila = client.get("/itinerary/uploads").json()[0]
    assert fila["uploaded_by"] == "GLAGO"
    assert fila["filename"] == "W25.csv"
    assert fila["effective_from"] == "2026-06-03"
    assert fila["estacion"] == "SPJC"
    assert fila["alcance"] == "desde"
    assert fila["filas_aceptadas"] == 2
    assert fila["uploaded_at"]


def test_la_carga_de_grilla_historica_tambien_deja_procedencia():
    client = _fresh_client()
    _login(client)

    # Una grilla histórica mínima, en el formato viejo separado por ';'
    encabezado = (
        "N°;HORA;D. ATS;VUELO;ADEP;DEP;ETA AIRCON;ETA CALC;SLOT ARR DGAC;;"
        "ETD1;CTOT 1;SEC1;H. REV1;REV1;ETD2;CTOT 2;SEC2;H. REV2;REV2;"
        "ETD3;CTOT 3;SEC3;H. REV3;REV3;ETD4;CTOT 4;SEC4;DLA;DLA (min);OBSERVACIONES"
    )
    fila = "1;2229;;LPE2553;SPUR;2252;0003;0003;NO H. ITIN;;2250;2252;SxSEC;;;;;;;;;;;;;;;;0:02;2;"
    contenido = (encabezado + "\n" + fila + "\n").encode("cp1252")

    resp = client.post(
        "/flights/import-history",
        params={"sector": "SUR", "flight_date": "2023-03-14", "confirmado": True},
        files={"file": ("grilla.csv", contenido, "text/csv")},
    )
    assert resp.status_code == 200, resp.text

    with database.SessionLocal() as db:
        carga = (
            db.query(Importacion)
            .filter(Importacion.tipo == TipoImportacion.GRILLA_HISTORICA)
            .one()
        )
        assert carga.sector is not None
        assert carga.fecha_operacion is not None
        # y los campos propios del itinerario quedan vacíos
        assert carga.estacion is None
        assert carga.vigente_desde is None

        vuelos = db.query(Flight).filter(Flight.flight_date == carga.fecha_operacion).all()
        assert vuelos
        assert all(v.importacion_id == carga.id for v in vuelos)
        assert all(v.fila_origen is not None for v in vuelos)
