"""El catálogo de motivos tiene que poder explicar lo que hay en los vuelos.

El vuelo guarda el motivo como texto en `sec1..sec5` y `rev1..rev4`, sin FK
hacia el catálogo. Eso deja dos formas de romper la correspondencia, y las dos
existían:

- dar de alta un motivo que después no cabe donde hay que usarlo, porque el
  catálogo admitía 24 caracteres y el vuelo 16;
- borrar un motivo que algún vuelo ya tiene escrito, dejando un código sin
  explicación.

Estas pruebas fijan las dos reglas que lo impiden, más la unicidad de
`(categoria, codigo)`.
"""
from fastapi.testclient import TestClient


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


def test_rechaza_un_motivo_mas_largo_que_el_campo_del_vuelo():
    """Un motivo de 17 caracteres no entra en sec1..sec5.

    Antes el alta respondía 200 y el motivo quedaba en el catálogo sin poder
    usarse en ningún vuelo. Ahora la API lo rechaza con 422."""
    client = _fresh_client()
    _login(client)

    resp = client.post(
        "/reason-codes",
        json={"category": "SEC", "code": "S" * 17, "description": "demasiado largo"},
    )
    assert resp.status_code == 422, resp.text

    # el de 16 sí entra: el límite es el largo real del campo del vuelo
    resp = client.post("/reason-codes", json={"category": "SEC", "code": "S" * 16})
    assert resp.status_code == 200, resp.text


def test_no_admite_dos_motivos_con_la_misma_categoria_y_codigo():
    """Dar de alta uno que ya existe devuelve el existente, no crea otro."""
    client = _fresh_client()
    _login(client)

    primero = client.post(
        "/reason-codes", json={"category": "REV", "code": "RxPRUEBA", "description": "uno"}
    ).json()
    segundo = client.post(
        "/reason-codes", json={"category": "REV", "code": "RxPRUEBA", "description": "otro"}
    ).json()

    assert primero["id"] == segundo["id"]

    todos = client.get("/reason-codes", params={"category": "REV"}).json()
    iguales = [m for m in todos if m["code"] == "RxPRUEBA"]
    assert len(iguales) == 1


def test_el_mismo_codigo_puede_existir_en_las_dos_categorias():
    """La unicidad es del par, no del código: un SEC y un REV pueden
    compartir el texto sin ser el mismo motivo."""
    client = _fresh_client()
    _login(client)

    sec = client.post("/reason-codes", json={"category": "SEC", "code": "MISMOCOD"})
    rev = client.post("/reason-codes", json={"category": "REV", "code": "MISMOCOD"})
    assert sec.status_code == 200 and rev.status_code == 200
    assert sec.json()["id"] != rev.json()["id"]


def test_un_motivo_retirado_no_se_ofrece_pero_sigue_existiendo():
    """Se desactiva en vez de borrarse: los vuelos ya gestionados guardan el
    código en texto y el catálogo tiene que poder explicarlo."""
    from app import database
    from app.models import ReasonCode

    client = _fresh_client()
    _login(client)
    creado = client.post("/reason-codes", json={"category": "SEC", "code": "SxRETIRO"}).json()
    assert creado["activo"] is True

    with database.SessionLocal() as db:
        db.get(ReasonCode, creado["id"]).activo = False
        db.commit()

    # la lista que ve el operador ya no lo ofrece
    activos = client.get("/reason-codes", params={"category": "SEC"}).json()
    assert "SxRETIRO" not in [m["code"] for m in activos]

    # pero la fila sigue ahí, y la administración del catálogo la ve
    todos = client.get(
        "/reason-codes", params={"category": "SEC", "incluir_inactivos": True}
    ).json()
    retirado = next(m for m in todos if m["code"] == "SxRETIRO")
    assert retirado["activo"] is False
    assert retirado["id"] == creado["id"]


def test_volver_a_dar_de_alta_un_motivo_retirado_lo_reactiva():
    """Sin esto, el alta respondía 200 con una fila que la lista seguía sin
    mostrar: el operador pedía el motivo y no aparecía."""
    from app import database
    from app.models import ReasonCode

    client = _fresh_client()
    _login(client)
    creado = client.post("/reason-codes", json={"category": "REV", "code": "RxVUELVE"}).json()

    with database.SessionLocal() as db:
        db.get(ReasonCode, creado["id"]).activo = False
        db.commit()

    reactivado = client.post("/reason-codes", json={"category": "REV", "code": "RxVUELVE"}).json()
    assert reactivado["id"] == creado["id"]
    assert reactivado["activo"] is True

    activos = client.get("/reason-codes", params={"category": "REV"}).json()
    assert "RxVUELVE" in [m["code"] for m in activos]


def test_los_motivos_sembrados_quedan_activos():
    """La semilla no puede dejar el catálogo vacío para el operador: los
    motivos de seed_data tienen que salir en la lista por omisión."""
    client = _fresh_client()
    _login(client)

    sec = client.get("/reason-codes", params={"category": "SEC"}).json()
    rev = client.get("/reason-codes", params={"category": "REV"}).json()
    assert sec and rev
    assert all(m["activo"] for m in sec + rev)
