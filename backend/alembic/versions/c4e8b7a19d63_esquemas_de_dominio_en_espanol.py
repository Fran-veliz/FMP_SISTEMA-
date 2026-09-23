"""esquemas de dominio y nombres en español

Lleva el esquema al modelo que describe `diseno/ARQUITECTURA_DATOS_EN_ESPANOL.md`:
tablas y columnas en español, repartidas en esquemas por dominio.

Por qué hace falta: `app/models.py` ya declara los nombres y esquemas nuevos,
pero ninguna migración los creaba. Una instalación nueva funcionaba igual,
porque `scripts/init_db.py` la arma con `create_all` desde el modelo; la que
quedaba rota era la base YA VERSIONADA -- producción, y la que se restaura de un
respaldo -- donde `alembic upgrade head` dejaba las tablas en `public` y con los
nombres viejos. Ahí no fallaba una consulta: fallaban todas.

Todo lo que hace es de catálogo. RENAME TABLE, SET SCHEMA, RENAME COLUMN y
RENAME CONSTRAINT no reescriben datos ni recorren filas: en PostgreSQL cambian
una entrada del diccionario. Sobre la copia de producción (1.033.000 filas)
corre en segundos.

Cada paso comprueba antes de actuar, y no es por elegancia: `shift_logs.position`
YA estaba renombrada a `posicion` por una migración anterior, así que una lista
aplicada a ciegas fallaría en esa columna y dejaría la base a medio traducir.

Revision ID: c4e8b7a19d63
Revises: b3f6a1c8d472
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8b7a19d63'
down_revision: Union[str, Sequence[str], None] = 'b3f6a1c8d472'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Los seis esquemas de dominio. `informes` y `operaciones` se crean vacíos: el
# diseño los reserva para los contratos de lectura y para el tráfico real, y
# vale más que una instalación nueva y una migrada se vean iguales desde el
# primer día que ahorrarse dos CREATE SCHEMA.
ESQUEMAS = ("catalogos", "cargas", "itinerarios", "ctot", "informes", "operaciones")

# (tabla_vieja_en_public, esquema_destino, tabla_nueva)
TABLAS = (
    ("estaciones", "catalogos", "aeropuerto"),
    ("airport_codes", "catalogos", "codigo_aeropuerto"),
    ("flight_history", "ctot", "historial_vuelo"),
    ("flight_history_imports", "ctot", "importacion_historica"),
    ("itinerary_uploads", "cargas", "lote_carga"),
    ("reason_codes", "ctot", "motivo"),
    ("itinerary_entries", "itinerarios", "movimiento_programado"),
    ("controllers", "ctot", "operador"),
    ("flight_deletion_log", "ctot", "registro_eliminacion_vuelo"),
    ("shift_logs", "ctot", "turno"),
    ("flights", "ctot", "vuelo_gestionado"),
)

# (esquema, tabla_nueva, columna_vieja, columna_nueva)
COLUMNAS = (
    ("catalogos", "codigo_aeropuerto", "iata", "codigo_iata"),
    ("catalogos", "codigo_aeropuerto", "icao", "codigo_oaci"),
    ("catalogos", "codigo_aeropuerto", "name", "nombre"),
    ("catalogos", "codigo_aeropuerto", "city", "ciudad"),
    ("catalogos", "codigo_aeropuerto", "country", "pais"),

    ("ctot", "historial_vuelo", "flight_id", "vuelo_id"),
    ("ctot", "historial_vuelo", "field_name", "nombre_campo"),
    ("ctot", "historial_vuelo", "old_value", "valor_anterior"),
    ("ctot", "historial_vuelo", "new_value", "valor_nuevo"),
    ("ctot", "historial_vuelo", "operator_name", "nombre_operador"),
    ("ctot", "historial_vuelo", "changed_at", "cambiado_en"),

    ("ctot", "importacion_historica", "flight_date", "fecha_operacion"),
    ("ctot", "importacion_historica", "uploaded_at", "cargado_en"),
    ("ctot", "importacion_historica", "uploaded_by", "cargado_por"),
    ("ctot", "importacion_historica", "filename", "nombre_archivo"),

    ("cargas", "lote_carga", "uploaded_at", "cargado_en"),
    ("cargas", "lote_carga", "uploaded_by", "cargado_por"),
    ("cargas", "lote_carga", "filename", "nombre_archivo"),
    ("cargas", "lote_carga", "estacion", "codigo_aeropuerto"),
    ("cargas", "lote_carga", "effective_from", "vigente_desde"),

    ("ctot", "motivo", "category", "categoria"),
    ("ctot", "motivo", "code", "codigo"),
    ("ctot", "motivo", "description", "descripcion"),

    ("itinerarios", "movimiento_programado", "estacion", "codigo_aeropuerto"),
    ("itinerarios", "movimiento_programado", "flight_date", "fecha_itinerario"),
    ("itinerarios", "movimiento_programado", "effective_from", "vigente_desde"),
    ("itinerarios", "movimiento_programado", "call_sign", "indicativo"),
    ("itinerarios", "movimiento_programado", "direction", "tipo_movimiento"),
    ("itinerarios", "movimiento_programado", "aerodromo", "aerodromo_contraparte"),

    ("ctot", "operador", "name", "nombre"),
    ("ctot", "operador", "pin_salt", "sal_pin"),
    ("ctot", "operador", "pin_hash", "resumen_pin"),
    ("ctot", "operador", "read_only", "solo_lectura"),
    ("ctot", "operador", "can_export", "puede_exportar"),
    ("ctot", "operador", "export_max_weeks", "max_semanas_exportacion"),
    ("ctot", "operador", "view_year_only", "solo_anio_actual"),
    ("ctot", "operador", "failed_attempts", "intentos_fallidos"),
    ("ctot", "operador", "locked_until", "bloqueado_hasta"),

    ("ctot", "registro_eliminacion_vuelo", "original_flight_id", "vuelo_original_id"),
    ("ctot", "registro_eliminacion_vuelo", "flight_date", "fecha_operacion"),
    ("ctot", "registro_eliminacion_vuelo", "flight_snapshot", "copia_vuelo"),
    ("ctot", "registro_eliminacion_vuelo", "history_snapshot", "copia_historial"),
    ("ctot", "registro_eliminacion_vuelo", "deleted_at", "eliminado_en"),
    ("ctot", "registro_eliminacion_vuelo", "deleted_by", "eliminado_por"),
    ("ctot", "registro_eliminacion_vuelo", "deletion_reason", "motivo_eliminacion"),

    # `position` -> `posicion` NO figura acá: ya la renombró una migración
    # anterior. El paso comprueba antes de actuar, así que tampoco haría daño.
    ("ctot", "turno", "operator_name", "nombre_operador"),
    ("ctot", "turno", "start_time", "iniciado_en"),
    ("ctot", "turno", "end_time", "finalizado_en"),
    ("ctot", "turno", "duration_minutes", "duracion_minutos"),
    ("ctot", "turno", "handover_note", "nota_relevo"),
    ("ctot", "turno", "session_token", "token_sesion"),
    ("ctot", "turno", "last_seen_at", "ultima_actividad_en"),
    ("ctot", "turno", "read_only", "solo_lectura"),
    ("ctot", "turno", "can_export", "puede_exportar"),
    ("ctot", "turno", "export_max_weeks", "max_semanas_exportacion"),
    ("ctot", "turno", "view_year_only", "solo_anio_actual"),

    ("ctot", "vuelo_gestionado", "flight_date", "fecha_operacion"),
    ("ctot", "vuelo_gestionado", "slot_arr_dgac", "franja_arr_dgac"),
    ("ctot", "vuelo_gestionado", "updated_at", "actualizado_en"),
    ("ctot", "vuelo_gestionado", "updated_by", "actualizado_por"),
)

# (esquema, indice_viejo, indice_nuevo). Los índices viajan con la tabla al
# cambiarle el esquema, pero conservan su nombre: hay que traducirlos aparte o
# una migración futura que los nombre como el modelo no los encontraría.
INDICES = (
    ("ctot", "ix_flight_history_flight_id", "ix_historial_vuelo_vuelo_id"),
    ("ctot", "ix_flight_history_imports_flight_date", "ix_importacion_historica_fecha_operacion"),
    ("ctot", "ix_flight_history_imports_sector", "ix_importacion_historica_sector"),
    ("ctot", "ix_reason_codes_category", "ix_motivo_categoria"),
    ("ctot", "ix_flight_deletion_log_deleted_at", "ix_registro_eliminacion_vuelo_eliminado_en"),
    ("ctot", "ix_flight_deletion_log_flight_date", "ix_registro_eliminacion_vuelo_fecha_operacion"),
    ("ctot", "ix_flight_deletion_log_original_flight_id", "ix_registro_eliminacion_vuelo_vuelo_original_id"),
    ("ctot", "ix_flight_deletion_log_sector", "ix_registro_eliminacion_vuelo_sector"),
    ("ctot", "ix_flight_deletion_log_vuelo", "ix_registro_eliminacion_vuelo_vuelo"),
    ("ctot", "ix_flights_flight_date", "ix_vuelo_gestionado_fecha_operacion"),
    ("ctot", "ix_flights_sector", "ix_vuelo_gestionado_sector"),
    ("itinerarios", "ix_itinerary_entries_call_sign", "ix_movimiento_programado_indicativo"),
    ("itinerarios", "ix_itinerary_entries_effective_from", "ix_movimiento_programado_vigente_desde"),
    ("itinerarios", "ix_itinerary_entries_flight_date", "ix_movimiento_programado_fecha_itinerario"),
    # Creados por la migración anterior (b3f6a1c8d472), todavía con nombre viejo.
    ("itinerarios", "ix_itinerary_lookup", "ix_movimiento_programado_consulta"),
    ("itinerarios", "uq_itinerary_dedup", "uq_movimiento_programado_duplicado"),
)

# (esquema, tabla, restriccion_vieja, restriccion_nueva)
RESTRICCIONES = (
    ("catalogos", "aeropuerto", "estaciones_pkey", "pk_aeropuerto"),
    ("catalogos", "codigo_aeropuerto", "airport_codes_pkey", "pk_codigo_aeropuerto"),
    ("ctot", "historial_vuelo", "flight_history_flight_id_fkey", "fk_historial_vuelo_vuelo"),
    ("ctot", "historial_vuelo", "flight_history_pkey", "pk_historial_vuelo"),
    ("ctot", "importacion_historica", "flight_history_imports_pkey", "pk_importacion_historica"),
    ("cargas", "lote_carga", "itinerary_uploads_pkey", "pk_lote_carga"),
    ("ctot", "motivo", "reason_codes_pkey", "pk_motivo"),
    ("itinerarios", "movimiento_programado", "itinerary_entries_pkey", "pk_movimiento_programado"),
    ("ctot", "operador", "controllers_name_key", "uq_operador_nombre"),
    ("ctot", "operador", "controllers_pkey", "pk_operador"),
    ("ctot", "registro_eliminacion_vuelo", "flight_deletion_log_pkey", "pk_registro_eliminacion_vuelo"),
    ("ctot", "turno", "shift_logs_pkey", "pk_turno"),
    ("ctot", "turno", "uq_shift_logs_session_token", "uq_turno_token_sesion"),
    ("ctot", "vuelo_gestionado", "flights_pkey", "pk_vuelo_gestionado"),
    ("ctot", "vuelo_gestionado", "uq_flight_numero", "uq_vuelo_gestionado_numero"),
    # Creadas por la migración anterior (b3f6a1c8d472).
    ("itinerarios", "movimiento_programado", "fk_itinerary_entries_estacion", "fk_movimiento_programado_aeropuerto"),
    ("cargas", "lote_carga", "fk_itinerary_uploads_estacion", "fk_lote_carga_aeropuerto"),
)


def _hay_tabla(esquema: str, nombre: str) -> bool:
    """Se pregunta al catálogo, no al inspector de SQLAlchemy.

    El inspector guarda en caché lo que reflejó, y las dos migraciones corren
    dentro de la misma transacción (env.py no usa transaction_per_migration):
    la tabla que creó la migración anterior no aparecía, y el traslado se
    abortaba diciendo que la base no estaba en el estado esperado.
    """
    return op.get_bind().execute(
        sa.text("SELECT to_regclass(:ref)"), {"ref": f"{esquema}.{nombre}"}
    ).scalar() is not None


def _columnas(esquema: str, tabla: str) -> set[str]:
    filas = op.get_bind().execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :e AND table_name = :t"
        ),
        {"e": esquema, "t": tabla},
    )
    return {fila[0] for fila in filas}


def _renombrar_indice(esquema: str, viejo: str, nuevo: str) -> None:
    op.execute(
        "DO $$ BEGIN "
        f"IF to_regclass('{esquema}.{viejo}') IS NOT NULL THEN "
        f"ALTER INDEX {esquema}.{viejo} RENAME TO {nuevo}; "
        "END IF; END $$"
    )


def _renombrar_restriccion(esquema: str, tabla: str, viejo: str, nuevo: str) -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_constraint c "
        "JOIN pg_class r ON r.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = r.relnamespace "
        f"WHERE n.nspname = '{esquema}' AND r.relname = '{tabla}' "
        f"AND c.conname = '{viejo}') THEN "
        f"ALTER TABLE {esquema}.{tabla} RENAME CONSTRAINT {viejo} TO {nuevo}; "
        "END IF; END $$"
    )


def upgrade() -> None:
    """Upgrade schema."""
    # Un ALTER necesita ACCESS EXCLUSIVE. Si algo está tocando la tabla -- un
    # pg_dump nocturno la retiene toda su duración -- el ALTER espera, y
    # mientras espera encola detrás de sí a todas las consultas nuevas, aunque
    # sean lecturas. Mejor cortar en cinco segundos con un error legible que
    # dejar la base entera parada sin explicación.
    op.execute("SET lock_timeout = '5s'")
    # Ver la nota de alembic/env.py: el usuario de la base se llama igual que uno
    # de estos esquemas, así que lo que no se califique iría a parar ahí.
    op.execute("SET search_path TO public")

    for esquema in ESQUEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS {esquema}')

    for vieja, esquema, nueva in TABLAS:
        if _hay_tabla(esquema, nueva):
            continue  # ya trasladada
        if not _hay_tabla("public", vieja):
            raise RuntimeError(
                f"No está ni public.{vieja} ni {esquema}.{nueva}: la base no "
                "está en el estado que esta migración espera."
            )
        op.execute(f'ALTER TABLE public.{vieja} RENAME TO {nueva}')
        op.execute(f'ALTER TABLE public.{nueva} SET SCHEMA {esquema}')

    for esquema, tabla, viejo, nuevo in COLUMNAS:
        columnas = _columnas(esquema, tabla)
        if nuevo in columnas:
            continue  # ya renombrada
        if viejo not in columnas:
            raise RuntimeError(
                f"{esquema}.{tabla} no tiene ni {viejo!r} ni {nuevo!r}."
            )
        op.execute(f'ALTER TABLE {esquema}.{tabla} RENAME COLUMN {viejo} TO {nuevo}')

    for esquema, viejo, nuevo in INDICES:
        _renombrar_indice(esquema, viejo, nuevo)
    for esquema, tabla, viejo, nuevo in RESTRICCIONES:
        _renombrar_restriccion(esquema, tabla, viejo, nuevo)

    # Columna nueva: distingue el vuelo que canceló el itinerario del que
    # canceló el operador a mano. Sin ella, al recargar el itinerario se
    # "restauraba" un vuelo que una persona había cancelado con dato fresco de
    # la compañía. Con server_default, agregarla no recorre las 224.373 filas.
    if "cancelado_por_itinerario" not in _columnas("ctot", "vuelo_gestionado"):
        op.add_column(
            "vuelo_gestionado",
            sa.Column(
                "cancelado_por_itinerario", sa.Boolean(), nullable=False,
                server_default=sa.text("false"),
            ),
            schema="ctot",
        )


def downgrade() -> None:
    """Downgrade schema. Deshace la traducción y devuelve todo a `public`."""
    op.execute("SET lock_timeout = '5s'")
    op.drop_column("vuelo_gestionado", "cancelado_por_itinerario", schema="ctot")

    for esquema, tabla, viejo, nuevo in RESTRICCIONES:
        _renombrar_restriccion(esquema, tabla, nuevo, viejo)
    for esquema, viejo, nuevo in INDICES:
        _renombrar_indice(esquema, nuevo, viejo)
    for esquema, tabla, viejo, nuevo in COLUMNAS:
        op.execute(f'ALTER TABLE {esquema}.{tabla} RENAME COLUMN {nuevo} TO {viejo}')
    for vieja, esquema, nueva in TABLAS:
        op.execute(f'ALTER TABLE {esquema}.{nueva} SET SCHEMA public')
        op.execute(f'ALTER TABLE public.{nueva} RENAME TO {vieja}')

    # Los esquemas quedan sin tablas, pero no necesariamente vacíos: en una base
    # creada con `create_all` los tipos enumerados (sector, posicion_enum,
    # tipo_movimiento_enum) nacen dentro del esquema de su tabla y siguen ahí
    # después de devolverla a `public`.
    #
    # Así que se intenta borrarlos y, si algo quedó dentro, se los deja en pie
    # sin fallar. RESTRICT solo protege de llevarse por delante lo ajeno; que
    # además abortara el downgrade entero por un esquema vacío de tablas sería
    # dejar sin salida justo a quien está intentando retroceder.
    for esquema in ESQUEMAS:
        op.execute(
            "DO $$ BEGIN "
            f"EXECUTE 'DROP SCHEMA IF EXISTS {esquema} RESTRICT'; "
            "EXCEPTION WHEN dependent_objects_still_exist THEN NULL; "
            "END $$"
        )
