from datetime import date, time
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import database
from app.models import ItineraryEntry


@pytest.fixture
def client():
    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/shifts/clock-in",
        json={"operator_name": "GLAGO", "position": "FMP SUR", "pin": "1234"},
    )
    assert response.status_code == 200
    client.headers["X-Shift-Token"] = response.json()["session_token"]
    return client


@pytest.mark.parametrize("endpoint", ["preview", "upload"])
def test_excel_usa_la_hora_del_aeropuerto_seleccionado(client, endpoint):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append([
        "NUMERO DE VUELO", "D / A", "ORIGEN / DESTINO", "FECHA UTC",
        "HORA APROBADA CUZ",
    ])
    sheet.append(["LPE123", "A", "LIM", date(2026, 6, 3), time(14, 30)])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    response = client.post(
        f"/itinerary/{endpoint}",
        params={
            "effective_from": "2026-06-03", "estacion": " spzo ",
            "alcance": "dia", "confirmado": True,
        },
        files={"file": ("cusco.xlsx", content.getvalue())},
    )
    assert response.status_code == 200, response.text
    assert response.json()["filas_aceptadas"] == 1
    with database.SessionLocal() as db:
        rows = db.execute(select(ItineraryEntry)).scalars().all()
        if endpoint == "upload":
            assert [(row.estacion, row.hora_utc) for row in rows] == [("SPZO", "1430")]
        else:
            assert response.json()["estacion"] == "SPZO"
            assert rows == []


def test_lookup_utiliza_el_codigo_de_estacion_normalizado(client):
    response = client.post(
        "/itinerary/upload",
        params={"effective_from": "2026-06-03", "estacion": "SPZO", "confirmado": True},
        files={"file": (
            "cusco.csv", "call_sign,direction,hora_utc,aerodromo\nLPE123,ARR,1430,SPJC\n",
        )},
    )
    assert response.status_code == 200, response.text
    response = client.get(
        "/itinerary/lookup",
        params={"flight_date": "2026-06-03", "estacion": " spzo ", "call_sign": "LPE123"},
    )
    assert response.status_code == 200
    assert response.json()["found"] is True
    assert response.json()["hora_utc"] == "1430"
