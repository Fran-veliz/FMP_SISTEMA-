import enum
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Sector(str, enum.Enum):
    SUR = "SUR"
    NOR = "NOR"


class Position(str, enum.Enum):
    FMP_SUR = "FMP SUR"
    FMP_NOR = "FMP NOR"
    FMP_CUSCO = "FMP CUSCO"


class Direction(str, enum.Enum):
    ARR = "ARR"
    DEP = "DEP"


class ReasonCategory(str, enum.Enum):
    SEC = "SEC"
    REV = "REV"


class Flight(Base):
    """Una fila de 'FormatoFMP SUR'/'FormatoFMP NOR'."""

    __tablename__ = "vuelo_gestionado"
    __table_args__ = (
        # Evita filas duplicadas si dos POST /flights concurrentes para el
        # mismo sector/fecha leen el mismo MAX(numero_fila) antes de que el
        # primero confirme -- ver create_flight, que reintenta ante choque.
        UniqueConstraint(
            "sector", "fecha_operacion", "numero_fila", name="uq_vuelo_gestionado_numero_fila"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sector: Mapped[Sector] = mapped_column(Enum(Sector), nullable=False, index=True)
    flight_date: Mapped[date_type] = mapped_column("fecha_operacion", Date, nullable=False, index=True)
    # Correlativo de la fila DENTRO de la grilla de este sector y este día:
    # MAX+1 al crear, y la grilla ordena por él. No es el número del vuelo, no
    # identifica al vuelo y no es comparable entre días ni entre sectores --
    # la fila 1 del SUR y la fila 1 del NOR del mismo día no tienen relación.
    # Se llamaba `numero`, al lado de la columna `vuelo`, y todo el mundo lo
    # leía como el número del vuelo.
    numero_fila: Mapped[int] = mapped_column(Integer, nullable=False)

    hora: Mapped[str | None] = mapped_column(String(4))
    d_ats: Mapped[str | None] = mapped_column(String(16))
    vuelo: Mapped[str] = mapped_column(String(16), nullable=False)
    adep: Mapped[str | None] = mapped_column(String(8))
    dep: Mapped[str | None] = mapped_column(String(4))

    eta_aircon: Mapped[str | None] = mapped_column(String(4))
    eta_aircon_calculado: Mapped[str | None] = mapped_column(String(4))
    slot_arr_dgac: Mapped[str | None] = mapped_column("franja_arr_dgac", String(16))

    # Los niveles CTOT viven en `vuelo_revision`, una fila por nivel.
    #
    # Aca hubo 23 columnas -- etd1..etd5, ctot1..ctot5, sec1..sec5 y
    # h_rev1..rev4 -- que se conservaron un tiempo como dato original
    # congelado, para poder comparar la descomposicion contra lo que habia.
    # Esa paridad se comprobo contra produccion sobre los 224.379 vuelos, con
    # cero diferencias, y las columnas se retiraron (migracion e5c81a6b9d34).
    #
    # `etd1`, `ctot1`, `sec1`... siguen funcionando igual como atributos: son
    # propiedades sobre las filas de revision (ver el bloque NIVELES al final
    # de este modulo). Por eso la grilla, la exportacion, el recalculo, el
    # historial y los importadores nunca tuvieron que cambiar.
    revisiones: Mapped[list["FlightRevision"]] = relationship(
        back_populates="vuelo",
        cascade="all, delete-orphan",
        order_by="FlightRevision.numero_revision",
        lazy="selectin",
    )

    dla_minutos: Mapped[float | None] = mapped_column(Float)
    observaciones: Mapped[str | None] = mapped_column(String(500))
    # Lo pone y lo quita el operador desde la grilla, y nada más. La carga de
    # itinerario ya no cancela vuelos por su cuenta -- ver ItineraryUpload.
    cancelado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Sin default/onupdate automático a propósito: SQLAlchemy invocaría
    # datetime.utcnow() crudo, sin pasar por app.clock.now_utc() (corregido
    # contra NTP) -- se setea explícitamente en recompute_flight().
    updated_at: Mapped[datetime] = mapped_column("actualizado_en", DateTime, nullable=False)
    # `updated_by` guarda el NOMBRE y se conserva: es lo que quedó registrado en
    # los vuelos históricos, y para muchos de ellos no hay ninguna fila de
    # `operador` a la que apuntar. `actualizado_por_id` es la referencia
    # canónica para todo lo nuevo. Nula = autor legado sin conciliar, no
    # "nadie". Ver la migración d7f1c4a92b38.
    updated_by: Mapped[str | None] = mapped_column("actualizado_por", String(64))
    updated_by_id: Mapped[int | None] = mapped_column(
        "actualizado_por_id",
        ForeignKey("operador.id", name="fk_vuelo_gestionado_operador"),
        index=True,
    )

    # De qué carga histórica salió este vuelo, y de qué renglón del archivo.
    # Nulas en los vuelos que creó un operador desde la grilla: esos no vienen
    # de ninguna carga. Ver Importacion.
    importacion_id: Mapped[int | None] = mapped_column(
        ForeignKey("importacion.id", name="fk_vuelo_gestionado_importacion"),
        index=True,
    )
    fila_origen: Mapped[int | None] = mapped_column(Integer)


class FlightRevision(Base):
    """Un nivel de negociación CTOT de un vuelo: qué ETD se pidió, qué CTOT se
    asignó y por qué.

    Antes eran 23 columnas en `vuelo_gestionado` (`etd1..ctot5`, `sec1..sec5`,
    `h_rev1..rev4`). No era una violación formal de 1FN -- son escalares --,
    pero sí una colección lógica limitada por columnas: cuando un vuelo
    necesitó un quinto nivel hubo que hacer una migración con ALTER TABLE
    (e3f7a91c25b8) en vez de un INSERT. El sexto habría necesitado otra.

    **El motivo de revisión pertenece al nivel que se abre, no al que se
    cierra.** `h_rev1/rev1` eran el motivo con el que se pasaba del nivel 1 al
    2, así que viajan con el nivel 2 y no con el 1. Es la misma lectura que ya
    hacía la grilla (FlightGrid.tsx). Por eso el nivel 1 no tiene motivo de
    revisión: no hay nivel anterior que lo justifique.

    Corregir una digitación modifica este nivel y queda en el historial; una
    revisión funcional nueva crea otra fila. Son dos acciones distintas y no
    hay que confundirlas: cinco correcciones de tipeo no son cinco
    negociaciones con la aerolínea.
    """

    __tablename__ = "vuelo_revision"
    __table_args__ = (
        UniqueConstraint(
            "vuelo_gestionado_id", "numero_revision", name="uq_vuelo_revision_nivel"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vuelo_gestionado_id: Mapped[int] = mapped_column(
        ForeignKey("vuelo_gestionado.id", name="fk_vuelo_revision_vuelo", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Ordinal del nivel, desde 1. No es un id ni un contador global.
    numero_revision: Mapped[int] = mapped_column(Integer, nullable=False)

    etd: Mapped[str | None] = mapped_column(String(4))
    ctot: Mapped[str | None] = mapped_column(String(4))

    # Motivo de secuencia (SEC): por qué este nivel quedó en esta posición.
    # Se guardan el texto y la referencia, igual que con el operador: el texto
    # es lo que quedó registrado y hay códigos históricos que no están en el
    # catálogo, así que la referencia nula significa "motivo legado sin
    # conciliar" y no "sin motivo".
    motivo_secuencia: Mapped[str | None] = mapped_column(String(16))
    motivo_secuencia_id: Mapped[int | None] = mapped_column(
        ForeignKey("motivo.id", name="fk_vuelo_revision_motivo_secuencia"),
        index=True,
    )

    # Motivo de revisión (REV) y su hora: por qué se ABRIÓ este nivel.
    hora_revision: Mapped[str | None] = mapped_column(String(4))
    motivo_revision: Mapped[str | None] = mapped_column(String(16))
    motivo_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("motivo.id", name="fk_vuelo_revision_motivo_revision"),
        index=True,
    )

    # Nulos en los niveles migrados: no se puede saber cuándo ni quién registró
    # una revisión de 2023, y atribuirla al último autor del vuelo o al momento
    # de la migración sería inventar el dato.
    creado_en: Mapped[datetime | None] = mapped_column(DateTime)
    creado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("operador.id", name="fk_vuelo_revision_operador"), index=True
    )

    vuelo: Mapped["Flight"] = relationship(back_populates="revisiones")


class FlightHistory(Base):
    """Historial de cambios por vuelo: qué campo cambió, quién y cuándo."""

    __tablename__ = "historial_vuelo"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flight_id: Mapped[int] = mapped_column("vuelo_id", ForeignKey("vuelo_gestionado.id", name="fk_historial_vuelo_vuelo", ondelete="CASCADE"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column("nombre_campo", String(32), nullable=False)
    old_value: Mapped[str | None] = mapped_column("valor_anterior", String(500))
    new_value: Mapped[str | None] = mapped_column("valor_nuevo", String(500))
    operator_name: Mapped[str | None] = mapped_column("nombre_operador", String(64))
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("operador.id", name="fk_historial_vuelo_operador"), index=True
    )
    # Sin default automático a propósito -- ver comentario en Flight.updated_at.
    changed_at: Mapped[datetime] = mapped_column("cambiado_en", DateTime, nullable=False)


class FlightDeletionLog(Base):
    """Auditoría de vuelos eliminados desde la grilla. El borrado real en
    `flights` es permanente y se lleva el historial en cascada (ver
    FlightHistory.flight_id), así que antes de borrar se guarda acá una foto
    completa del vuelo y de su historial de cambios -- ver delete_flight en
    routers/flights.py. Solo lectura desde afuera: no hay endpoint propio,
    se consulta directo en la base para auditorías."""

    __tablename__ = "registro_eliminacion_vuelo"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_flight_id: Mapped[int] = mapped_column("vuelo_original_id", Integer, nullable=False, index=True)
    sector: Mapped[Sector] = mapped_column(Enum(Sector), nullable=False, index=True)
    flight_date: Mapped[date_type] = mapped_column("fecha_operacion", Date, nullable=False, index=True)
    vuelo: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    flight_snapshot: Mapped[dict] = mapped_column("copia_vuelo", JSON, nullable=False)
    history_snapshot: Mapped[list] = mapped_column("copia_historial", JSON, nullable=False)
    # Sin default automático a propósito -- ver comentario en Flight.updated_at.
    deleted_at: Mapped[datetime] = mapped_column("eliminado_en", DateTime, nullable=False, index=True)
    deleted_by: Mapped[str | None] = mapped_column("eliminado_por", String(64))
    deleted_by_id: Mapped[int | None] = mapped_column(
        "eliminado_por_id",
        ForeignKey("operador.id", name="fk_registro_eliminacion_operador"),
        index=True,
    )
    deletion_reason: Mapped[str | None] = mapped_column("motivo_eliminacion", String(300))


class ShiftLog(Base):
    """Reemplaza la hoja BITACORA: registro de turnos por operador."""

    __tablename__ = "turno"
    __table_args__ = (
        UniqueConstraint("token_sesion", name="uq_turno_token_sesion"),
        # Una persona, un turno abierto -- impuesto por la BASE, no solo por el
        # código. clock_in consulta si ya hay uno y después crea: entre esos dos
        # pasos hay un instante, y dos peticiones simultáneas (doble clic, dos
        # pestañas, un reintento de red) podían pasar las dos la consulta y
        # crear dos turnos con dos tokens válidos.
        #
        # Índice parcial: solo aplica a los turnos SIN cerrar. Los cerrados
        # pueden repetirse tantas veces como jornadas trabaje la persona.
        Index(
            "uq_turno_abierto_por_operador",
            "nombre_operador",
            unique=True,
            postgresql_where=text("finalizado_en IS NULL"),
            sqlite_where=text("finalizado_en IS NULL"),
        ),
        # La misma regla, ahora sobre la referencia. Los dos índices conviven
        # durante la transición: el de arriba sigue cubriendo los turnos
        # legados sin conciliar, este cubre todo lo que se abra de acá en
        # adelante y no se deja engañar por dos grafías del mismo nombre.
        Index(
            "uq_turno_abierto_por_operador_id",
            "operador_id",
            unique=True,
            postgresql_where=text("finalizado_en IS NULL AND operador_id IS NOT NULL"),
            sqlite_where=text("finalizado_en IS NULL AND operador_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # La identidad del turno es el ID del operador; `operator_name` queda como
    # el texto que se registró en su momento. Hasta aquí la regla "un turno
    # abierto por persona" se imponía sobre el texto, así que dos grafías del
    # mismo nombre eran dos personas distintas para la base.
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("operador.id", name="fk_turno_operador"), index=True
    )
    operator_name: Mapped[str] = mapped_column("nombre_operador", String(64), nullable=False)
    position: Mapped[Position] = mapped_column(
        Enum(Position, name="posicion_enum"), name="posicion", nullable=False
    )
    start_time: Mapped[datetime] = mapped_column("iniciado_en", DateTime, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column("finalizado_en", DateTime)
    # La duración es `finalizado_en - iniciado_en`: un derivado, y por eso ya
    # no se calcula ni se guarda. Esta columna conserva lo que quedó grabado en
    # los turnos cerrados antes del cambio, que NO siempre coincide con la
    # resta: los cierres automáticos la fijaron hasta la última actividad real
    # y no hasta el instante del cierre. Donde difiere, el valor guardado es
    # evidencia de lo que la bitácora dijo, no un cálculo a rehacer.
    duracion_original_minutos: Mapped[float | None] = mapped_column(
        "duracion_minutos", Float
    )
    # Nota de relevo: al cerrar el turno, el operador FMP deja escrito qué
    # operaciones quedan pendientes; el siguiente la ve al iniciar su turno.
    handover_note: Mapped[str | None] = mapped_column("nota_relevo", String(1000))
    # Token de sesión emitido al hacer clock-in: lo que autoriza al frontend
    # a crear/editar/borrar vuelos, cargar itinerario, etc. mientras el turno
    # siga abierto (ver app/auth.py). Único por turno, nulo tras cerrarlo.
    # Copia legada de la huella del pase vigente. La identidad de la sesión
    # ahora es una fila de `ctot.sesion`: un turno tiene VARIAS sesiones a lo
    # largo de su vida, porque cada reenganche rota el pase, y esta columna
    # solo podía guardar la última -- las anteriores se perdían al pisarse.
    # Se sigue escribiendo mientras dure la transición, para que un rollback
    # del código no deje a nadie afuera, y se retira cuando `sesion` esté
    # probada en producción.
    session_token: Mapped[str | None] = mapped_column("token_sesion", String(64))
    # Última vez que este turno autorizó una petición. El token deja de valer
    # tras un tiempo sin uso (ver auth.SHIFT_IDLE_TIMEOUT_HOURS): sin esto, un
    # turno que nadie cerró -- se cortó la luz, cerraron el navegador -- dejaba
    # su token vivo indefinidamente en el localStorage de esa computadora.
    last_seen_at: Mapped[datetime | None] = mapped_column("ultima_actividad_en", DateTime)
    # Copiado de Controller.read_only al hacer clock-in (no se recalcula en
    # cada request): un perfil de solo lectura (ej. DGAC) puede iniciar y
    # cerrar turno normalmente, pero ningún endpoint de escritura lo acepta.
    read_only: Mapped[bool] = mapped_column("solo_lectura", Boolean, default=False, nullable=False)

    # El turno se cerró solo por inactividad: nadie tocó "cerrar turno".
    # Importa distinguirlo, porque la bitácora es un registro operativo y no
    # puede dar a entender que alguien hizo un cierre que en realidad no hizo.
    cerrado_automaticamente: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )

    # Copiados de Controller igual que read_only: el alcance de consulta queda
    # congelado en el turno, así un cambio de nómina a mitad de sesión no
    # amplía ni recorta lo que ese turno ya podía ver.
    can_export: Mapped[bool] = mapped_column("puede_exportar", Boolean, default=True, nullable=False)
    export_max_weeks: Mapped[int | None] = mapped_column("max_semanas_exportacion", Integer)
    view_year_only: Mapped[bool] = mapped_column("solo_anio_actual", Boolean, default=False, nullable=False)

    sesiones: Mapped[list["Sesion"]] = relationship(
        back_populates="turno", order_by="Sesion.iniciada_en"
    )


class Sesion(Base):
    """Una sesión autenticada: el pase que autoriza al frontend a escribir.

    Vivía como una columna del turno (`turno.token_sesion`), y ahí había un
    hecho 1:N metido en un solo lugar: **cada reenganche rota el pase**, así
    que un turno tiene varias sesiones a lo largo de su vida y la columna solo
    podía guardar la última. Las anteriores se perdían al pisarse, sin dejar
    registro de cuándo empezó ni cuándo dejó de valer cada una.

    Turno y sesión además tienen ciclos de vida distintos, y conviene no
    confundirlos: el turno es un hecho operativo cerrado y permanente -- quién
    trabajó, en qué posición, desde cuándo hasta cuándo, con qué nota de
    relevo -- y va a la bitácora. La sesión es un mecanismo de autenticación
    que se emite, se usa, se rota y se revoca.

    Del pase solo se guarda su huella, nunca el valor: en la base no hay con
    qué autenticarse. Ver `app.auth.huella_token`.
    """

    __tablename__ = "sesion"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_sesion_token_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Sin cascada de borrado: la sesión es evidencia de un acceso y tiene que
    # sobrevivir a cualquier limpieza del turno.
    turno_id: Mapped[int] = mapped_column(
        ForeignKey("turno.id", name="fk_sesion_turno"), nullable=False, index=True
    )
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("operador.id", name="fk_sesion_operador"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    iniciada_en: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Última vez que esta sesión autorizó una petición. Se escribe con
    # granularidad de minuto para no hacer un UPDATE por request.
    ultima_actividad_en: Mapped[datetime | None] = mapped_column(DateTime)
    # Nula = vigente. No se borra la fila al revocar: el registro de que esa
    # sesión existió y hasta cuándo valió es lo que la hace auditable.
    revocada_en: Mapped[datetime | None] = mapped_column(DateTime)
    # "cierre_turno" | "rotacion" | "inactividad"
    motivo_revocacion: Mapped[str | None] = mapped_column(String(32))

    turno: Mapped["ShiftLog"] = relationship(back_populates="sesiones")


class ItineraryEntry(Base):
    """Itinerario de vuelos (temporada W25 o carga diaria tipo hoja INFO).
    Usada para resolver ADEP/DEP y SLOT ARR DGAC (equivalente a los
    VLOOKUP(D3, INFO!B:C, ...) del Excel). `effective_from` marca desde
    qué actualización de DGAC viene esta fila, para poder reemplazar solo
    "de una fecha en adelante" sin tocar el histórico ya volado."""

    __tablename__ = "movimiento_programado"
    __table_args__ = (
        # La búsqueda real (_lookup_itinerario / lookup_itinerary) siempre
        # filtra por los tres juntos; sin este índice compuesto, Postgres
        # solo puede usar uno de los índices individuales y filtra el resto
        # en memoria.
        Index("ix_movimiento_programado_consulta", "codigo_aeropuerto", "fecha_itinerario", "indicativo", "tipo_movimiento"),
        # Impide filas de itinerario exactamente repetidas (mismo vuelo, hora,
        # aeródromo, aeronave y servicio, para la misma fecha y vigencia).
        # Algunos Excel de origen traían el bloque INFO pegado muchas veces
        # (ver import_historico.cargar_dia, que ahora también deduplica): sin
        # esta restricción, Postgres las aceptaba en silencio e inflaba los
        # conteos. NULLS NOT DISTINCT trata dos NULL como iguales (Postgres 15+)
        # para que un aeródromo/servicio vacío no burle la unicidad.
        Index(
            "uq_movimiento_programado_duplicado",
            "codigo_aeropuerto", "fecha_itinerario", "vigente_desde", "indicativo", "tipo_movimiento",
            "hora_utc", "aerodromo_contraparte", "tipo_aeronave", "tipo_servicio",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # De qué aeródromo es este itinerario. No confundir con `aerodromo`, que
    # es el OTRO extremo del vuelo (origen si ARR, destino si DEP).
    estacion: Mapped[str] = mapped_column("codigo_aeropuerto", 
        # La clave foránea va nombrada a propósito. Sin nombre, Postgres le
        # inventa uno y la migración que quiera tocarla después no tiene cómo
        # nombrarla: create_all y Alembic terminan creando la misma
        # restricción con nombres distintos según por qué camino se armó la
        # base, y el downgrade falla en una y funciona en la otra.
        String(4),
        # Apunta al maestro. Antes referenciaba `catalogos.aeropuerto`, una de
        # las tres tablas que guardaban el mismo aeródromo.
        ForeignKey("aerodromo.codigo_oaci", name="fk_movimiento_programado_aerodromo"),
        # Sin index=True: la columna admite dos valores y hoy todas las filas
        # tienen el mismo, así que un índice propio no se usaría nunca y solo
        # encarecería cada inserción.
        nullable=False,
    )
    flight_date: Mapped[date_type] = mapped_column("fecha_itinerario", Date, nullable=False, index=True)
    effective_from: Mapped[date_type] = mapped_column("vigente_desde", Date, nullable=False, index=True)
    call_sign: Mapped[str] = mapped_column("indicativo", String(16), nullable=False, index=True)
    direction: Mapped[Direction] = mapped_column("tipo_movimiento", Enum(Direction, name="tipo_movimiento_enum"), nullable=False)
    hora_utc: Mapped[str | None] = mapped_column(String(4))
    aerodromo: Mapped[str | None] = mapped_column("aerodromo_contraparte", String(8))
    tipo_aeronave: Mapped[str | None] = mapped_column(String(8))
    tipo_servicio: Mapped[str | None] = mapped_column(String(4))
    asientos: Mapped[int | None] = mapped_column(Integer)

    # De qué ejecución de carga salió esta fila, y de qué renglón del archivo.
    # Antes no existía: la procedencia había que deducirla por estación y
    # fecha de vigencia, y deshacer una carga era borrar por coincidencia.
    # Nulas en lo cargado antes de la migración f3a8c41e7d92 -- eso ya no se
    # puede reconstruir, y adivinar a qué carga perteneció cada fila sería
    # inventar la trazabilidad que justamente faltaba.
    importacion_id: Mapped[int | None] = mapped_column(
        ForeignKey("importacion.id", name="fk_movimiento_programado_importacion"),
        index=True,
    )
    fila_origen: Mapped[int | None] = mapped_column(Integer)


class TipoImportacion(str, enum.Enum):
    ITINERARIO = "itinerario"
    GRILLA_HISTORICA = "grilla_historica"


class Importacion(Base):
    """Una ejecución de carga: qué archivo entró, cuándo, quién lo subió y con
    qué resultado.

    Unifica dos tablas que eran casi la misma: `cargas.lote_carga` (subida de
    itinerario DGAC) e `ctot.importacion_historica` (subida de una grilla FMP
    ya cerrada). Compartían `cargado_en`, `cargado_por`, `nombre_archivo`,
    `filas_aceptadas` y `filas_rechazadas`, y se diferenciaban solo en los
    campos propios de cada tipo, que ahora conviven acá y se distinguen por
    `tipo`.

    Lo que se gana no es ahorrar una tabla: es que lo cargado pueda apuntar a
    su carga. Antes ni `movimiento_programado` ni `vuelo_gestionado` tenían
    referencia a la ejecución que los creó, así que deshacer una carga había
    que hacerlo por coincidencia de sector y fecha -- es literalmente lo que
    hace `borrar_historicos` en scripts/import_historico.py, a falta de un
    identificador al que agarrarse. Con `importacion_id`, deshacer una carga es
    un DELETE por identificador.
    """

    __tablename__ = "importacion"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[TipoImportacion] = mapped_column(
        # `values_callable` no es decorativo: sin él SQLAlchemy guarda el
        # NOMBRE del miembro ("ITINERARIO") y el tipo de Postgres tiene como
        # etiquetas los VALORES ("itinerario"), que es lo que creó la
        # migración f3a8c41e7d92 y lo que ya tienen las 3.481 filas cargadas.
        # Toda inserción por el ORM moría con "invalid input value for enum
        # tipo_importacion_enum", así que ni la carga de itinerario ni la de
        # grilla histórica podían completarse.
        #
        # Los demás enums no lo necesitan porque su nombre y su valor
        # coinciden (SUR, ARR, SEC); `posicion_enum` difiere pero la base
        # guarda los nombres, que es justo lo que el comportamiento por
        # defecto escribe.
        Enum(
            TipoImportacion,
            name="tipo_importacion_enum",
            values_callable=lambda tipo: [miembro.value for miembro in tipo],
        ),
        nullable=False,
        index=True,
    )

    # Sin default automático a propósito -- ver comentario en Flight.updated_at.
    cargado_en: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cargado_por: Mapped[str | None] = mapped_column(String(64))
    cargado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("operador.id", name="fk_importacion_operador"), index=True
    )
    nombre_archivo: Mapped[str | None] = mapped_column(String(200))

    # --- propios de una carga de itinerario ---
    # A qué estación se le cargó el itinerario.
    estacion: Mapped[str | None] = mapped_column(
        "codigo_aeropuerto",
        String(4),
        # La clave foránea va nombrada a propósito. Sin nombre, Postgres le
        # inventa uno y la migración que quiera tocarla después no tiene cómo
        # nombrarla: create_all y Alembic terminan creando la misma
        # restricción con nombres distintos según por qué camino se armó la
        # base, y el downgrade falla en una y funciona en la otra.
        ForeignKey("aerodromo.codigo_oaci", name="fk_importacion_aerodromo"),
    )
    vigente_desde: Mapped[date_type | None] = mapped_column(Date)
    # "desde" = reemplaza de vigente_desde en adelante (actualización DGAC de
    # temporada). "dia" = reemplaza SOLO ese día, sin tocar los posteriores:
    # es la carga diaria suelta.
    alcance: Mapped[str | None] = mapped_column(String(8))

    # --- propios de una carga de grilla histórica ---
    sector: Mapped[Sector | None] = mapped_column(Enum(Sector), index=True)
    fecha_operacion: Mapped[date_type | None] = mapped_column(Date, index=True)

    filas_aceptadas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filas_rechazadas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ReasonCode(Base):
    """Motivos SEC1-4 (motivo de secuencia) y REV1-3 (motivo de revisión).
    Editable en caliente: si aparece un motivo nuevo que no está en la
    lista, se agrega aquí en vez de tocar código.

    Vive en `catalogos` y no en `ctot`: es un catálogo, como aeropuerto y
    codigo_aeropuerto, y no un dato de la gestión de un vuelo."""

    __tablename__ = "motivo"
    __table_args__ = (
        # Nada impedía dos filas SEC/DEMORA. El router ya consultaba antes de
        # insertar, pero entre la consulta y el INSERT hay un instante, y un
        # INSERT directo por psql no pasaba por ese control. La regla la
        # impone la base.
        UniqueConstraint("categoria", "codigo", name="uq_motivo_categoria_codigo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[ReasonCategory] = mapped_column("categoria", Enum(ReasonCategory, name="categoria_motivo_enum"), nullable=False, index=True)
    # 16 y no 24: es el largo con el que el motivo se guarda en el vuelo
    # (sec1..sec5, rev1..rev4). Con 24 se podía dar de alta un motivo que
    # después no cabía donde hay que usarlo, y el alta parecía exitosa.
    code: Mapped[str] = mapped_column("codigo", String(16), nullable=False)
    description: Mapped[str | None] = mapped_column("descripcion", String(200))
    # Un motivo que se deja de usar se desactiva, no se borra: los vuelos ya
    # gestionados guardan su código y el catálogo tiene que poder explicarlo.
    activo: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )


class Controller(Base):
    """Nómina de operadores FMP con PIN: cierra el hueco de que hoy cualquiera
    con acceso a la red puede "ser" cualquier operador con solo elegir su
    nombre de una lista (ver app/auth.py). El PIN nunca se guarda en texto
    plano -- ver app.auth.hash_pin. No es un sistema de usuarios completo
    (sin roles, sin recuperación de PIN por email): cambiar un PIN es una
    UPDATE directa en esta tabla, a propósito, siguiendo cómo ya se maneja
    el resto de la administración de este sistema."""

    __tablename__ = "operador"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Identificador de acceso: lo que el operador teclea para iniciar turno.
    # Se llamaba `nombre` y cumplía dos papeles a la vez -- identificar y
    # mostrarse --, así que corregir la grafía de un nombre cambiaba con qué
    # se entra al sistema y dejaba desalineadas las copias de texto que
    # guardan turno, historial y autorías.
    usuario: Mapped[str] = mapped_column("usuario", String(64), unique=True, nullable=False)
    # El nombre real de la persona, para mostrar. Puede repetirse (dos
    # homónimos son dos cuentas distintas) y puede corregirse sin tocar el
    # acceso ni las autorías ya registradas.
    nombre_completo: Mapped[str | None] = mapped_column(String(120))
    # Una persona que deja la FMP se desactiva, no se borra: sus turnos,
    # cambios y cargas siguen en la bitácora y tienen que poder atribuirse.
    # Un perfil inactivo no puede iniciar turno -- ver routers/shifts.py.
    activo: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    pin_salt: Mapped[str] = mapped_column("sal_pin", String(32), nullable=False)
    # Argon2id incluye algoritmo, parámetros y sal dentro del valor codificado.
    pin_hash: Mapped[str] = mapped_column("resumen_pin", String(255), nullable=False)
    # Perfil genérico de solo lectura (ej. DGAC): puede iniciar turno y ver
    # todo en vivo, pero require_writable_shift (app/auth.py) rechaza
    # cualquier escritura sin importar qué endpoint la pida.
    read_only: Mapped[bool] = mapped_column("solo_lectura", Boolean, default=False, nullable=False)

    # Límites de consulta (pensados para los perfiles de la DGAC; los
    # operadores FMP los llevan en su valor permisivo). Se copian al turno al
    # hacer clock-in -- ver ShiftLog.
    can_export: Mapped[bool] = mapped_column("puede_exportar", Boolean, default=True, nullable=False)
    # Cuántas semanas hacia atrás puede descargar. None = sin límite.
    export_max_weeks: Mapped[int | None] = mapped_column("max_semanas_exportacion", Integer)
    # Si solo puede consultar fechas del año en curso.
    view_year_only: Mapped[bool] = mapped_column("solo_anio_actual", Boolean, default=False, nullable=False)

    # Protección contra fuerza bruta del PIN: 4 dígitos son 10.000
    # combinaciones, así que sin límite de intentos el PIN no es una defensa
    # real (ver DECISIONES.md §5). Se resetean al primer ingreso correcto.
    failed_attempts: Mapped[int] = mapped_column("intentos_fallidos", Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column("bloqueado_hasta", DateTime)


class Aerodromo(Base):
    """Un aeródromo, una sola vez.

    El mismo hecho vivía en tres tablas sin relación entre sí:

    - `catalogos.aeropuerto` (PK OACI) -- solo las estaciones con itinerario
      propio, con su capacidad declarada.
    - `catalogos.codigo_aeropuerto` (PK IATA) -- los extremos de rutas,
      incluidos los extranjeros, con la equivalencia IATA/OACI, ciudad y país.
    - `aerodromos` del Portal ATFM -- el catálogo geográfico del Perú, el más
      completo de los tres: región, coordenadas, elevación, tipo.

    Ninguna era autoridad sobre las otras y no había clave foránea entre
    ellas: `aeropuerto` tiene PK OACI y `codigo_aeropuerto` PK IATA, así que ni
    siquiera se podían cruzar sin adivinar.

    **Este maestro cubre la unión, no la intersección.** Tiene que incluir los
    extremos de rutas y los aeródromos extranjeros que solo estaban en
    `codigo_aeropuerto`: si se armara solo con las estaciones, la carga de
    itinerario perdería los códigos que necesita traducir.

    **Aparecer acá no convierte a un aeródromo en estación CTOT.** Esa es otra
    cosa, y la dice `es_estacion_ctot`: que Lima aparezca en una equivalencia
    IATA junto a Ámsterdam no los pone al mismo nivel operativo.

    Las columnas geográficas quedan nulas hasta que se consolide la base con el
    Portal, que es de donde vienen. **No se inventan coordenadas** para
    completar los registros que no las tienen: nulo es la respuesta correcta.
    """

    __tablename__ = "aerodromo"
    __table_args__ = (
        UniqueConstraint("codigo_oaci", name="uq_aerodromo_codigo_oaci"),
        UniqueConstraint("codigo_iata", name="uq_aerodromo_codigo_iata"),
    )

    # Clave técnica estable: permite corregir un código mal cargado sin
    # rehacer todas las referencias, que es lo que hoy obligaría una PK que es
    # el propio código.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nulos admitidos porque `codigo_aeropuerto.codigo_oaci` lo era: hay
    # equivalencias cargadas que solo tienen IATA. Al menos uno de los dos
    # códigos tiene que estar -- lo impone un CHECK en la migración.
    codigo_oaci: Mapped[str | None] = mapped_column(String(4))
    codigo_iata: Mapped[str | None] = mapped_column(String(4))

    # El nombre oficial y el operativo son cosas distintas y se conservan por
    # separado: el del catálogo internacional viene en inglés, y el documento
    # del PDA es oficial y en castellano.
    nombre_oficial: Mapped[str | None] = mapped_column(String(200))
    nombre_operativo: Mapped[str | None] = mapped_column(String(100))

    ciudad: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(100))
    pais: Mapped[str | None] = mapped_column(String(4))

    latitud: Mapped[float | None] = mapped_column(Float)
    longitud: Mapped[float | None] = mapped_column(Float)
    elevacion_ft: Mapped[int | None] = mapped_column(Integer)
    tipo: Mapped[str | None] = mapped_column(String(16))
    servicio_comercial: Mapped[bool | None] = mapped_column(Boolean)

    # ¿Tiene itinerario propio en el sistema? Es lo que antes significaba
    # existir en `catalogos.aeropuerto` con `activa = true`.
    es_estacion_ctot: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # Un aeródromo referenciado se desactiva, no se borra.
    activo: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    capacidades: Mapped[list["CapacidadAerodromo"]] = relationship(
        back_populates="aerodromo", order_by="CapacidadAerodromo.vigente_desde"
    )


class CapacidadAerodromo(Base):
    """Cuántas operaciones por hora admite un aeródromo, declarado y con
    vigencia.

    Estaba como una columna de `catalogos.aeropuerto`, y antes de eso como la
    constante 49 en el código de /forecast -- que es el límite de Lima, no de
    cualquier aeródromo.

    Tiene entidad propia por dos razones. La primera es que **la capacidad no
    es un atributo del lugar sino una declaración con fecha**: cambia de manera
    excepcional, y cuando cambia hay que poder decir desde cuándo rige la nueva
    sin perder la anterior. La segunda es que **es el mismo hecho que declara
    el PDA del Portal** en `pda_aerodromo.declarada`: son dos valores
    editables por separado que deberían ser uno. Esta tabla es donde van a
    converger cuando se consolide la base.

    `valor` nulo significa **sin declarar**, no cero: la DGAC no publicó la
    cifra de ese aeródromo. El cero del PDA tiene ese mismo significado y no se
    reinterpreta como cierre operacional al migrarlo.
    """

    __tablename__ = "capacidad_aerodromo"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aerodromo_id: Mapped[int] = mapped_column(
        ForeignKey("aerodromo.id", name="fk_capacidad_aerodromo"),
        nullable=False,
        index=True,
    )
    # Operaciones/hora al 100 % (arribos + despegues). Nulo = sin declarar.
    valor: Mapped[int | None] = mapped_column(Integer)
    # Desglose de la configuración de pistas que sustenta esa cifra, y el
    # título del régimen. Vienen del PDA, donde el FMU ya los escribía.
    configuracion: Mapped[str | None] = mapped_column(String(200))
    regimen: Mapped[str | None] = mapped_column(String(120))

    # Nulo en lo migrado: no se sabe desde cuándo regía la cifra que estaba en
    # la columna, y fecharla en el día de la migración sería inventarlo.
    vigente_desde: Mapped[date_type | None] = mapped_column(Date)
    # Nulo = sigue vigente.
    vigente_hasta: Mapped[date_type | None] = mapped_column(Date)
    # De dónde salió: "DGAC", "FMU", "migracion_catalogo"...
    fuente: Mapped[str | None] = mapped_column(String(64))

    aerodromo: Mapped["Aerodromo"] = relationship(back_populates="capacidades")


# ---------------------------------------------------------------------------
# NIVELES: `flight.etd1`, `flight.ctot3`, `flight.rev2`...
#
# Los niveles son filas de `vuelo_revision`, pero el resto del sistema los
# sigue viendo como los 23 atributos planos de siempre. No es azúcar: es lo
# que permite cambiar dónde viven los datos sin reescribir a la vez el
# recálculo (services/recompute.py), la exportación, la detección de cambios
# del historial, los dos importadores, los esquemas de la API y la grilla.
# Cada uno de esos lugares seguía leyendo y escribiendo `etd1`, y todos
# siguen funcionando igual.
#
# El traslado es el que ya hacía la grilla: `h_revN`/`revN` son el motivo con
# el que se ABRE el nivel N+1, así que se guardan en la fila del nivel N+1.
# ---------------------------------------------------------------------------

#: atributo plano -> (nivel al que pertenece, campo de la fila de revisión)
CAMPOS_DE_NIVEL: dict[str, tuple[int, str]] = {}
for _n in range(1, 6):
    CAMPOS_DE_NIVEL[f"etd{_n}"] = (_n, "etd")
    CAMPOS_DE_NIVEL[f"ctot{_n}"] = (_n, "ctot")
    CAMPOS_DE_NIVEL[f"sec{_n}"] = (_n, "motivo_secuencia")
    if _n < 5:
        # h_rev1 abre el nivel 2, h_rev4 abre el nivel 5. No hay h_rev5:
        # no existe un nivel 6 que abrir.
        CAMPOS_DE_NIVEL[f"h_rev{_n}"] = (_n + 1, "hora_revision")
        CAMPOS_DE_NIVEL[f"rev{_n}"] = (_n + 1, "motivo_revision")
del _n

#: el nivel más alto que admiten los atributos planos. Las filas de
#: `vuelo_revision` no tienen este techo -- es justamente lo que se ganó --,
#: pero la grilla y la exportación todavía muestran hasta el quinto.
NIVEL_MAXIMO_PLANO = 5


def _nivel(flight: "Flight", numero: int) -> "FlightRevision | None":
    for revision in flight.revisiones:
        if revision.numero_revision == numero:
            return revision
    return None


def _nivel_o_crear(flight: "Flight", numero: int) -> "FlightRevision":
    revision = _nivel(flight, numero)
    if revision is None:
        revision = FlightRevision(numero_revision=numero)
        flight.revisiones.append(revision)
    return revision


def _propiedad_de_nivel(numero: int, campo: str) -> property:
    def leer(self: "Flight") -> str | None:
        revision = _nivel(self, numero)
        return None if revision is None else getattr(revision, campo)

    def escribir(self: "Flight", valor: str | None) -> None:
        # Escribir un vacío en un nivel que no existe no lo crea: así un
        # payload que manda las 23 columnas en blanco -- lo que hace la grilla
        # al guardar una fila nueva -- no deja cinco filas de revisión vacías.
        if valor in (None, "") and _nivel(self, numero) is None:
            return
        setattr(_nivel_o_crear(self, numero), campo, valor)

    return property(leer, escribir)


for _plano, (_numero, _campo) in CAMPOS_DE_NIVEL.items():
    setattr(Flight, _plano, _propiedad_de_nivel(_numero, _campo))
del _plano, _numero, _campo
