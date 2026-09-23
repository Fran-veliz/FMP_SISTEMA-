import pytest
from fastapi.testclient import TestClient

from app.services.flight_history_import import InvalidFlightHistoryFile, parse_flight_history_csv

_HEADER_FIELDS = [
    "N°", "HORA", "D. ATS", "VUELO", "ADEP", "DEP", "ETA AIRCON", "ETA AIRCON CALCULADO",
    "SLOT ARR DGAC", "SLOT ARR DGAC", "ETD 1", "CTOT 1", "SEC1", "H. REV1", "REV1",
    "ETD2", "CTOT 2", "SEC2", "H. REV2", "REV2", "ETD3", "CTOT 3", "SEC3", "H. REV3", "REV3",
    "ETD4", "CTOT 4", "SEC4", "DLA", "DLA (min)", "OBSERVACIONES",
]

# nombre de kwarg -> índice de columna (para armar filas sin contar ';' a mano)
_COL = {
    "numero": 0, "hora": 1, "d_ats": 2, "vuelo": 3, "adep": 4, "dep": 5,
    "eta_aircon": 6, "eta_aircon_calculado": 7, "slot_arr_dgac": 8,
    "etd1": 10, "ctot1": 11, "sec1": 12, "h_rev1": 13, "rev1": 14,
    "etd2": 15, "ctot2": 16, "sec2": 17, "h_rev2": 18, "rev2": 19,
    "etd3": 20, "ctot3": 21, "sec3": 22, "h_rev3": 23, "rev3": 24,
    "etd4": 25, "ctot4": 26, "sec4": 27,
    "dla": 28, "dla_min": 29, "observaciones": 30,
}


def _row(**fields) -> str:
    cells = [""] * len(_HEADER_FIELDS)
    for key, value in fields.items():
        cells[_COL[key]] = str(value)
    return ";".join(cells)


def _csv(*rows: str) -> bytes:
    header = ";".join(_HEADER_FIELDS) + "\n"
    body = "\n".join(rows) + "\n"
    return (header + body).encode("cp1252")


def test_parse_basic_row():
    content = _csv(_row(
        numero=1, hora="2229", vuelo="LPE2553", adep="SPUR", dep="2252",
        eta_aircon="0003", eta_aircon_calculado="0003",
        slot_arr_dgac="NO H. ITIN", etd1="2250", ctot1="2252", sec1="SxSEC",
        dla="0:02", dla_min="2",
    ))
    result = parse_flight_history_csv(content)
    assert result.filas_aceptadas == 1
    assert result.filas_rechazadas == 0
    row = result.rows[0]
    assert row.vuelo == "LPE2553"
    assert row.hora == "2229"
    assert row.adep == "SPUR"
    assert row.dep == "2252"
    assert row.etd1 == "2250"
    assert row.ctot1 == "2252"
    assert row.sec1 == "SxSEC"
    assert row.slot_arr_dgac is None  # "NO H. ITIN" -> vacío
    assert row.dla_minutos == 2.0
    assert row.cancelado is False


def test_colon_time_format_normalized_to_hhmm():
    content = _csv(_row(
        numero=2, hora="2251", vuelo="LPE2296", adep="SPJR", dep="2323",
        eta_aircon="0021", eta_aircon_calculado="00:18",
        slot_arr_dgac="23:05", etd1="2332", ctot1="2332",
        h_rev1="2315", rev1="RxCIADEP", etd2="2328", ctot2="2329", sec2="SxSEC",
        dla="0:01", dla_min="1",
    ))
    result = parse_flight_history_csv(content)
    row = result.rows[0]
    assert row.eta_aircon_calculado == "0018"
    assert row.slot_arr_dgac == "2305"
    assert row.h_rev1 == "2315"
    assert row.rev1 == "RxCIADEP"


def test_cancelado_detected_from_observaciones():
    content = _csv(_row(
        numero=3, hora="1200", vuelo="LPE1", adep="SPUR", dep="1220",
        observaciones="CANCELADO POR CLIMA",
    ))
    result = parse_flight_history_csv(content)
    assert result.rows[0].cancelado is True


def test_blank_trailing_rows_are_skipped_not_errors():
    content = _csv(
        _row(numero=1, hora="2229", vuelo="LPE2553", adep="SPUR", dep="2252", dla="0:02", dla_min="2"),
        *[";" * (len(_HEADER_FIELDS) - 1) for _ in range(5)],
    )
    result = parse_flight_history_csv(content)
    assert result.filas_aceptadas == 1
    assert result.filas_rechazadas == 0


def test_missing_required_headers_rejected():
    with pytest.raises(InvalidFlightHistoryFile):
        parse_flight_history_csv("FOO;BAR\n1;2\n".encode("utf-8"))


def test_bad_dla_reported_not_fatal():
    content = _csv(_row(
        numero=1, hora="2229", vuelo="LPE2553", adep="SPUR", dep="2252", dla_min="no-es-numero",
    ))
    result = parse_flight_history_csv(content)
    assert result.filas_aceptadas == 0
    assert result.filas_rechazadas == 1


def _fresh_client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _login(client: TestClient) -> dict:
    resp = client.post(
        "/shifts/clock-in", json={"operator_name": "GLAGO", "position": "FMP NOR", "pin": "1234"}
    )
    assert resp.status_code == 200, resp.text
    shift = resp.json()
    client.headers["X-Shift-Token"] = shift["session_token"]
    return shift


def test_preview_returns_rows_without_saving_anything():
    client = _fresh_client()
    _login(client)

    content = _csv(
        _row(numero=1, hora="2229", vuelo="LPE2553", adep="SPUR", dep="2252",
             etd1="2250", ctot1="2252", sec1="SxSEC", dla="0:02", dla_min="2"),
        _row(numero=2, hora="2251", vuelo="LPE2296", adep="SPJR", dep="2323",
             observaciones="CANCELADO POR CIA", dla="0", dla_min="0"),
    )
    resp = client.post(
        "/flights/import-history/preview",
        params={"sector": "NOR", "flight_date": "2023-01-01"},
        files={"file": ("dia.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    preview = resp.json()
    assert preview["filas_aceptadas"] == 2
    assert preview["vuelos_existentes"] == 0
    assert [r["vuelo"] for r in preview["rows"]] == ["LPE2553", "LPE2296"]
    assert preview["rows"][0]["ctot1"] == "2252"
    assert preview["rows"][1]["cancelado"] is True

    # la vista previa NO guarda nada: ni vuelos ni registro de importación
    assert client.get("/flights", params={"sector": "NOR", "flight_date": "2023-01-01"}).json() == []
    assert client.get("/flights/import-history/uploads").json() == []


def test_preview_warns_about_existing_flights():
    client = _fresh_client()
    _login(client)

    content = _csv(_row(numero=1, hora="2229", vuelo="LPE2553", adep="SPUR", dep="2252", dla="0", dla_min="0"))
    resp = client.post(
        "/flights/import-history",
        params={"sector": "NOR", "flight_date": "2023-01-01"},
        files={"file": ("dia.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        "/flights/import-history/preview",
        params={"sector": "NOR", "flight_date": "2023-01-01"},
        files={"file": ("dia.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["vuelos_existentes"] == 1


def test_import_history_endpoint_creates_flights_and_logs_upload():
    client = _fresh_client()
    _login(client)

    content = _csv(
        _row(numero=1, hora="2229", vuelo="LPE2553", adep="SPUR", dep="2252",
             etd1="2250", ctot1="2252", sec1="SxSEC", dla="0:02", dla_min="2"),
        _row(numero=2, hora="2251", vuelo="LPE2296", adep="SPJR", dep="2323",
             eta_aircon_calculado="00:18", slot_arr_dgac="23:05",
             etd1="2332", ctot1="2332", h_rev1="2315", rev1="RxCIADEP",
             etd2="2328", ctot2="2329", sec2="SxSEC", dla="0:01", dla_min="1"),
    )
    resp = client.post(
        "/flights/import-history",
        params={"sector": "NOR", "flight_date": "2023-01-01"},
        files={"file": ("dia.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["filas_aceptadas"] == 2
    assert report["filas_rechazadas"] == 0

    flights = client.get("/flights", params={"sector": "NOR", "flight_date": "2023-01-01"}).json()
    assert len(flights) == 2
    assert {f["numero_fila"] for f in flights} == {1, 2}
    assert {f["vuelo"] for f in flights} == {"LPE2553", "LPE2296"}

    uploads = client.get("/flights/import-history/uploads").json()
    assert len(uploads) == 1
    assert uploads[0]["filas_aceptadas"] == 2


def test_http_y_script_conservan_los_mismos_valores_historicos():
    from dataclasses import asdict
    from datetime import date
    from pathlib import Path

    from app import database
    from app.models import Flight, Sector
    from scripts.import_historico import DayResult, FileInfo, cargar_dia

    # Los resultados originales no coinciden con los que daría el cálculo
    # actual. Ambos importadores deben conservarlos, incluso el DEP legado.
    content = _csv(_row(
        hora="1210", d_ats="TEST", vuelo="LPE111", adep="SPZO", dep="ND",
        eta_aircon="1400", eta_aircon_calculado="1917", slot_arr_dgac="2355",
        etd1="1230", ctot1="1240", sec1="SxSEC", h_rev1="1215", rev1="RxCIADEP",
        etd2="1245", ctot2="1255", sec2="SxTFC", h_rev2="1220", rev2="RxTMI",
        etd3="1300", ctot3="1310", sec3="SxACC", h_rev3="1225", rev3="RxMET",
        etd4="1315", ctot4="1325", sec4="SxSEC", dla_min="91,25",
        observaciones="CANCELADO POR CIA",
    ))
    row = parse_flight_history_csv(content).rows[0]
    client = _fresh_client()
    _login(client)
    response = client.post(
        "/flights/import-history",
        params={"sector": "NOR", "flight_date": "2023-01-01"},
        files={"file": ("dia.csv", content, "text/csv")},
    )
    assert response.status_code == 200, response.text

    fecha = date(2023, 1, 1)
    dia = DayResult(
        info=FileInfo(Path("dia.xlsx"), fecha, fecha),
        grid_rows={"SUR": [row]},
    )
    with database.SessionLocal() as db:
        cargar_dia(db, dia, solo_vuelos=True)
        db.commit()
        http = db.query(Flight).filter_by(sector=Sector.NOR).one()
        script = db.query(Flight).filter_by(sector=Sector.SUR).one()
        for campo, valor in asdict(row).items():
            if campo != "fila_origen":
                assert getattr(http, campo) == valor, campo
                assert getattr(script, campo) == valor, campo

        # Cada entrada conserva sus propias reglas de autoría y procedencia.
        assert http.fila_origen == 2
        assert script.fila_origen == 1
        assert http.importacion_id != script.importacion_id
        assert http.updated_by == "GLAGO" and http.updated_by_id is not None
        assert script.updated_by == "IMPORT HISTÓRICO" and script.updated_by_id is None


def test_import_history_rejects_duplicate_date_sector():
    client = _fresh_client()
    _login(client)

    content = _csv(_row(numero=1, hora="2229", vuelo="LPE2553", adep="SPUR", dep="2252", dla="0", dla_min="0"))
    resp = client.post(
        "/flights/import-history",
        params={"sector": "NOR", "flight_date": "2023-01-01"},
        files={"file": ("dia.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text

    resp2 = client.post(
        "/flights/import-history",
        params={"sector": "NOR", "flight_date": "2023-01-01"},
        files={"file": ("dia.csv", content, "text/csv")},
    )
    assert resp2.status_code == 409


def test_import_history_blocked_for_read_only_profile():
    client = _fresh_client()
    resp = client.post(
        "/shifts/clock-in",
        json={"operator_name": "DGAC1", "position": "FMP CUSCO", "pin": "2580"},
    )
    assert resp.status_code == 200, resp.text
    client.headers["X-Shift-Token"] = resp.json()["session_token"]

    content = _csv(_row(numero=1, hora="2229", vuelo="LPE2553", adep="SPUR", dep="2252", dla="0", dla_min="0"))
    resp = client.post(
        "/flights/import-history",
        params={"sector": "NOR", "flight_date": "2023-01-01"},
        files={"file": ("dia.csv", content, "text/csv")},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# El CONSOLIDADO del día (.xlsx)
#
# Lo que la FMP archiva es el libro, no el CSV. La carga por pantalla solo
# aceptaba CSV, así que el operador que soltaba el CONSOLIDADO recibía
# "No se pudo leer el archivo (codificación desconocida)": un .xlsx es un ZIP
# y sus bytes chocan con los que cp1252 no define. El mensaje hablaba de
# codificaciones y el problema era el formato.
# --------------------------------------------------------------------------- #

def _libro_consolidado() -> bytes:
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "JUNIO 3 CONSOLIDADO(1).xlsx"
    return fixture.read_bytes()


def test_el_libro_consolidado_se_lee_por_sector():
    """Del mismo libro salen las dos grillas, según el sector que se pida."""
    from app.services.flight_history_import import parse_flight_history_file

    libro = _libro_consolidado()
    sur = parse_flight_history_file(libro, "SUR")
    nor = parse_flight_history_file(libro, "NOR")

    assert sur.filas_aceptadas > 0
    assert nor.filas_aceptadas > 0
    # Son dos grillas distintas, no la misma hoja leída dos veces.
    assert [f.vuelo for f in sur.rows] != [f.vuelo for f in nor.rows]


def test_el_formato_se_decide_por_el_contenido_y_no_por_el_nombre():
    """Un libro renombrado a .csv sigue siendo un libro.

    El operador no tiene por qué saber cuál de los dos formatos le toca, y la
    extensión miente seguido: los archivos que llegan por correo vuelven
    renombrados más de una vez.
    """
    from app.services.flight_history_import import (
        es_libro_excel, parse_flight_history_file,
    )

    libro = _libro_consolidado()
    assert es_libro_excel(libro)
    assert not es_libro_excel(_csv(_row(vuelo="LPE111", hora="1210")))
    # No mira el nombre en ningún momento: recibe solo los bytes.
    assert parse_flight_history_file(libro, "SUR").filas_aceptadas > 0


def test_un_libro_sin_la_hoja_del_sector_lo_dice_con_las_hojas_que_hay():
    """El error tiene que decir qué falta y qué había, no 'no se pudo leer'."""
    import io

    import openpyxl
    import pytest

    from app.services.flight_history_import import (
        InvalidFlightHistoryFile, parse_flight_history_file,
    )

    wb = openpyxl.Workbook()
    wb.active.title = "OTRA COSA"
    buf = io.BytesIO()
    wb.save(buf)

    with pytest.raises(InvalidFlightHistoryFile) as exc:
        parse_flight_history_file(buf.getvalue(), "SUR")
    assert "FormatoFMP SUR" in str(exc.value)
    assert "OTRA COSA" in str(exc.value)


def test_un_archivo_que_no_es_ninguno_de_los_dos_lo_dice_claro():
    """El mensaje que originó todo esto: hablaba de codificaciones."""
    import pytest

    from app.services.flight_history_import import (
        InvalidFlightHistoryFile, parse_flight_history_file,
    )

    # Bytes que ninguna de las dos codificaciones acepta y que no son un ZIP.
    with pytest.raises(InvalidFlightHistoryFile) as exc:
        parse_flight_history_file(b"\x81\x8d\x8f\x90\x9d", "SUR")
    mensaje = str(exc.value)
    assert "CSV" in mensaje and "Excel" in mensaje


def test_el_endpoint_acepta_el_libro_consolidado():
    """La carga por pantalla, con el archivo que el operador tiene de verdad."""
    from app.models import Flight, Sector
    from app import database

    client = _fresh_client()
    _login(client)
    respuesta = client.post(
        "/flights/import-history",
        params={"sector": "SUR", "flight_date": "2023-06-03"},
        files={
            "file": (
                "JUNIO 3 CONSOLIDADO.xlsx",
                _libro_consolidado(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["filas_aceptadas"] > 0

    with database.SessionLocal() as db:
        cargados = db.query(Flight).filter_by(sector=Sector.SUR).count()
        assert cargados == respuesta.json()["filas_aceptadas"]


# --------------------------------------------------------------------------- #
# Volver a cargar un día ya cargado
#
# Antes el 409 era un callejón sin salida: la única salida que ofrecía era
# borrar los vuelos uno por uno desde la grilla, y un día tiene cerca de cien.
# Cuando llega el CONSOLIDADO corregido de una jornada, recargarlo es la
# operación normal.
# --------------------------------------------------------------------------- #

def _cargar(client, sector: str, fecha: str, contenido: bytes, reemplazar: bool = False):
    params = {"sector": sector, "flight_date": fecha}
    if reemplazar:
        params["reemplazar"] = "true"
    return client.post(
        "/flights/import-history",
        params=params,
        files={"file": ("dia.csv", contenido, "text/csv")},
    )


def test_sin_reemplazar_sigue_rechazando_y_explica_como_seguir():
    client = _fresh_client()
    _login(client)
    contenido = _csv(_row(vuelo="LPE111", hora="1210"))

    assert _cargar(client, "SUR", "2023-01-01", contenido).status_code == 200

    repetida = _cargar(client, "SUR", "2023-01-01", contenido)
    assert repetida.status_code == 409
    # El mensaje tiene que nombrar la salida, no dejar al operador sin opción.
    assert "reemplazar" in repetida.json()["detail"].lower()


def test_reemplazar_deja_solo_los_vuelos_del_archivo_nuevo():
    from app import database
    from app.models import Flight, Sector

    client = _fresh_client()
    _login(client)
    _cargar(client, "SUR", "2023-01-01", _csv(
        _row(vuelo="VIEJO1", hora="1210"),
        _row(vuelo="VIEJO2", hora="1300"),
    ))

    nuevo = _csv(_row(vuelo="NUEVO1", hora="1400"))
    respuesta = _cargar(client, "SUR", "2023-01-01", nuevo, reemplazar=True)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["vuelos_reemplazados"] == 2
    assert respuesta.json()["filas_aceptadas"] == 1

    with database.SessionLocal() as db:
        vuelos = db.query(Flight).filter_by(sector=Sector.SUR).all()
        assert [v.vuelo for v in vuelos] == ["NUEVO1"]
        # La numeración arranca de nuevo: si quedara la de los borrados, la
        # restricción (sector, fecha, numero_fila) chocaría.
        assert [v.numero_fila for v in vuelos] == [1]


def test_lo_reemplazado_queda_auditado_y_recuperable():
    """Reemplazar cien vuelos no puede dejar menos rastro que borrar uno."""
    from app import database
    from app.models import FlightDeletionLog

    client = _fresh_client()
    _login(client)
    _cargar(client, "SUR", "2023-01-01", _csv(_row(vuelo="VIEJO1", hora="1210")))
    _cargar(client, "SUR", "2023-01-01", _csv(_row(vuelo="NUEVO1", hora="1400")), reemplazar=True)

    with database.SessionLocal() as db:
        registros = db.query(FlightDeletionLog).all()
        assert len(registros) == 1
        registro = registros[0]
        assert registro.vuelo == "VIEJO1"
        assert "Reemplazado por la carga" in registro.deletion_reason
        assert registro.deleted_by == "GLAGO"
        # La foto completa permite reconstruir lo que había.
        assert registro.flight_snapshot["vuelo"] == "VIEJO1"
        assert registro.flight_snapshot["hora"] == "1210"


def test_reemplazar_no_toca_el_otro_sector():
    from app import database
    from app.models import Flight, Sector

    client = _fresh_client()
    _login(client)
    _cargar(client, "SUR", "2023-01-01", _csv(_row(vuelo="DEL_SUR", hora="1210")))
    _cargar(client, "NOR", "2023-01-01", _csv(_row(vuelo="DEL_NOR", hora="1220")))

    _cargar(client, "SUR", "2023-01-01", _csv(_row(vuelo="NUEVO_SUR", hora="1400")), reemplazar=True)

    with database.SessionLocal() as db:
        assert [v.vuelo for v in db.query(Flight).filter_by(sector=Sector.NOR).all()] == ["DEL_NOR"]
        assert [v.vuelo for v in db.query(Flight).filter_by(sector=Sector.SUR).all()] == ["NUEVO_SUR"]


def test_reemplazar_sobre_un_dia_vacio_no_rompe_ni_inventa_borrados():
    client = _fresh_client()
    _login(client)
    respuesta = _cargar(
        client, "SUR", "2023-01-01", _csv(_row(vuelo="LPE111", hora="1210")), reemplazar=True
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["vuelos_reemplazados"] == 0
