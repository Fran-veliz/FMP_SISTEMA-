"""devuelve el acceso a los esquemas a los lectores externos

Mover una tabla a otro esquema conserva sus permisos de tabla, pero no sirve de
nada si el rol no puede entrar al esquema nuevo: sin USAGE, PostgreSQL responde
"permission denied for schema" aunque el GRANT SELECT siga ahí. c4e8b7a19d63
creó los seis esquemas de dominio sin dárselo a nadie, así que el traslado le
cortó el acceso a todo lector externo sin quitarle un solo permiso.

El caso real es el Portal ATFM, que lee el itinerario de esta base con un rol
de solo lectura (`atfm_lector`) para dibujar la demanda prevista del PDA. Desde
la traducción, la lectura fallaba y el portal se quedaba sin curva -- sin error
a la vista, porque ante un fallo devuelve "no se sabe" en vez de inventar
demanda en un documento que se firma.

No se nombra ningún rol: se le da USAGE a quien YA tenga SELECT sobre alguna
tabla del esquema. Es exactamente a quien el traslado se lo quitó, y así el
arreglo vale para cualquier instalación sin que haya que enumerar los lectores
de cada una. En una base sin lectores externos -- un servidor nuevo -- no
encuentra nada y no hace nada.

Revision ID: e1a5c92f3d64
Revises: d2f7b6c14a80
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e1a5c92f3d64'
down_revision: Union[str, Sequence[str], None] = 'd2f7b6c14a80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ESQUEMAS = ("catalogos", "cargas", "itinerarios", "ctot", "informes", "operaciones")


def upgrade() -> None:
    """Upgrade schema."""
    lista = ", ".join(f"'{e}'" for e in ESQUEMAS)
    op.execute(
        "DO $$ DECLARE r record; BEGIN "
        "FOR r IN "
        "  SELECT DISTINCT n.nspname AS esquema, "
        "         pg_get_userbyid(a.grantee) AS rol "
        "  FROM pg_class c "
        "  JOIN pg_namespace n ON n.oid = c.relnamespace "
        "  CROSS JOIN LATERAL aclexplode(c.relacl) a "
        f" WHERE n.nspname IN ({lista}) "
        "    AND a.privilege_type = 'SELECT' "
        # grantee 0 es PUBLIC, que no se nombra en un GRANT ... TO %I; y al
        # dueño no hay nada que darle, entra a sus propios esquemas.
        "    AND a.grantee <> 0 "
        "    AND a.grantee <> c.relowner "
        "    AND NOT has_schema_privilege(a.grantee, n.oid, 'USAGE') "
        "LOOP "
        "  EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', r.esquema, r.rol); "
        "END LOOP; END $$"
    )


def downgrade() -> None:
    """Downgrade schema.

    A propósito no revoca nada. Esto no agrega un permiso nuevo: repone el
    acceso que el traslado de esquemas quitó sin querer. Quitarlo de vuelta
    dejaría al lector externo sin la tabla otra vez, y los esquemas los borra
    igual el downgrade de c4e8b7a19d63, que corre a continuación.
    """
