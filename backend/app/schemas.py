from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app import clock
from app.auth import SHIFT_MAX_HOURS
from app.models import Position, ReasonCategory, Sector


class FlightBase(BaseModel):
    hora: str | None = None
    d_ats: str | None = None
    vuelo: str
    adep: str | None = None
    dep: str | None = None
    eta_aircon: str | None = None
    etd1: str | None = None
    ctot1: str | None = None
    sec1: str | None = None
    h_rev1: str | None = None
    rev1: str | None = None
    etd2: str | None = None
    ctot2: str | None = None
    sec2: str | None = None
    h_rev2: str | None = None
    rev2: str | None = None
    etd3: str | None = None
    ctot3: str | None = None
    sec3: str | None = None
    h_rev3: str | None = None
    rev3: str | None = None
    etd4: str | None = None
    ctot4: str | None = None
    sec4: str | None = None
    h_rev4: str | None = None
    rev4: str | None = None
    etd5: str | None = None
    ctot5: str | None = None
    sec5: str | None = None
    observaciones: str | None = None


class FlightCreate(FlightBase):
    sector: Sector
    flight_date: date


class FlightUpdate(BaseModel):
    hora: str | None = None
    d_ats: str | None = None
    vuelo: str | None = None
    adep: str | None = None
    dep: str | None = None
    eta_aircon: str | None = None
    etd1: str | None = None
    ctot1: str | None = None
    sec1: str | None = None
    h_rev1: str | None = None
    rev1: str | None = None
    etd2: str | None = None
    ctot2: str | None = None
    sec2: str | None = None
    h_rev2: str | None = None
    rev2: str | None = None
    etd3: str | None = None
    ctot3: str | None = None
    sec3: str | None = None
    h_rev3: str | None = None
    rev3: str | None = None
    etd4: str | None = None
    ctot4: str | None = None
    sec4: str | None = None
    h_rev4: str | None = None
    rev4: str | None = None
    etd5: str | None = None
    ctot5: str | None = None
    sec5: str | None = None
    observaciones: str | None = None
    cancelado: bool | None = None
    updated_by: str | None = None


class FlightOut(FlightBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sector: Sector
    flight_date: date
    numero_fila: int
    eta_aircon_calculado: str | None = None
    slot_arr_dgac: str | None = None
    dla_minutos: float | None = None
    cancelado: bool = False
    updated_at: datetime
    updated_by: str | None = None


class FlightHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    field_name: str
    old_value: str | None = None
    new_value: str | None = None
    operator_name: str | None = None
    changed_at: datetime


class ShiftClockIn(BaseModel):
    operator_name: str = Field(min_length=1, max_length=64)
    position: Position
    # Sin tope, un "PIN" de megabytes llegaba igual hasta Argon2. El mínimo no
    # se sube más porque hay perfiles antiguos de 4 dígitos que deben poder
    # seguir entrando hasta que TI rote las credenciales.
    pin: str = Field(min_length=4, max_length=64)


class ShiftClockOut(BaseModel):
    handover_note: str | None = None


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    operator_name: str
    position: Position
    start_time: datetime
    end_time: datetime | None = None
    handover_note: str | None = None
    read_only: bool = False
    # True si lo cerró el sistema por inactividad y no el operador.
    cerrado_automaticamente: bool = False
    # Alcance del perfil, para que la interfaz no ofrezca lo que el backend
    # va a rechazar (ej. el botón de descarga a un perfil que no descarga).
    can_export: bool = True
    export_max_weeks: int | None = None
    view_year_only: bool = False

    @computed_field
    @property
    def duration_minutes(self) -> float | None:
        """Minutos trabajados, calculados. Era una columna con la resta ya
        hecha; se retiró porque es un derivado de `start_time` y `end_time`.

        Nula mientras el turno sigue abierto, igual que antes. En los cierres
        automáticos `end_time` es la última actividad real y no el instante del
        cierre, así que el cálculo da lo mismo que daba el valor grabado -- un
        turno olvidado el viernes y cerrado el lunes no reporta setenta y dos
        horas de trabajo que nadie hizo."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds() / 60

    @computed_field
    @property
    def horas_en_turno(self) -> float:
        """Horas que lleva abierto el turno (o que duró, si ya cerró).

        Se calcula con el reloj del servidor corregido por NTP (app.clock) y
        no con el de la computadora del operador: si esa máquina tiene la hora
        mal puesta, el aviso de turno excedido saltaría cuando no toca o no
        saltaría nunca."""
        fin = self.end_time or clock.now_utc()
        return round((fin - self.start_time).total_seconds() / 3600, 2)

    @computed_field
    @property
    def excede_maximo(self) -> bool:
        """El turno pasó el máximo previsto. Es un aviso, no un corte: la
        sesión sigue siendo válida (ver auth.SHIFT_MAX_HOURS)."""
        return self.horas_en_turno > SHIFT_MAX_HOURS

    @computed_field
    @property
    def maximo_horas(self) -> int:
        """El umbral, para que la interfaz no lo tenga hardcodeado aparte."""
        return SHIFT_MAX_HOURS


class ShiftAuthOut(ShiftOut):
    """Igual a ShiftOut + el token de sesión. Solo se devuelve al abrir el
    turno (clock-in): /shifts/active y /shifts (historial) siguen usando
    ShiftOut sin token, para no filtrarlo a quien solo está mirando la
    bitácora."""

    session_token: str


class ReasonCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: ReasonCategory
    code: str
    description: str | None = None
    activo: bool = True


class ReasonCodeCreate(BaseModel):
    category: ReasonCategory
    # El tope es el largo con el que el motivo se guarda en el vuelo. Antes el
    # catálogo admitía 24 caracteres: el alta respondía 200 y el motivo no
    # entraba después en sec1..sec5. Ahora la API lo rechaza con un 422 que
    # dice cuál es el límite.
    code: str = Field(min_length=1, max_length=16)
    description: str | None = Field(default=None, max_length=200)


class AirportCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    iata: str
    icao: str | None = None
    name: str | None = None
    city: str | None = None
    country: str | None = None


class EstacionOut(BaseModel):
    """Aeródromo del que la FMP recibe itinerario.

    No confundir con `AirportCodeOut`: ese es el mapeo IATA↔OACI de cualquier
    aeropuerto que aparezca como origen o destino de un vuelo. Este es el
    catálogo corto de los que tienen itinerario propio en el sistema."""

    model_config = ConfigDict(from_attributes=True)

    codigo_oaci: str
    # El nombre que muestra el desplegable: el operativo, que es como el FMP
    # nombra al aeródromo, y si no está, el oficial. Lo resuelve el router
    # -- ver list_estaciones -- porque en el maestro son dos columnas.
    nombre: str
    # Ya no es una columna del aeródromo: es la declaración vigente. Se resuelve
    # en el servicio -- ver itinerario.capacidad_declarada -- y este campo
    # queda para no cambiarle la forma a la respuesta.
    capacidad_declarada: int | None = None


class ItineraryLookup(BaseModel):
    found: bool
    fuente: str | None = None  # "hoy" | "dia_anterior"
    hora_utc: str | None = None
    aerodromo: str | None = None


class ItineraryRowError(BaseModel):
    row: int
    motivo: str


class ItineraryUploadReport(BaseModel):
    filas_aceptadas: int
    filas_rechazadas: int
    errores: list[ItineraryRowError]


class ItineraryUploadOut(BaseModel):
    """Una carga de itinerario del historial.

    Los nombres de los campos son los que la interfaz ya consumía. Se leen de
    `integracion.importacion`, que unificó los dos registros de carga: el
    contrato de la API no tenía por qué moverse con la tabla."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    uploaded_at: datetime = Field(validation_alias="cargado_en")
    uploaded_by: str | None = Field(default=None, validation_alias="cargado_por")
    filename: str | None = Field(default=None, validation_alias="nombre_archivo")
    # De qué aeródromo era el itinerario que se cargó. Desde que hay más de
    # una estación, el historial sin este dato no dice qué se reemplazó.
    estacion: str
    effective_from: date = Field(validation_alias="vigente_desde")
    # Cargas anteriores a esta función quedaron todas como "desde", que es lo
    # que efectivamente hicieron.
    alcance: str = "desde"
    filas_aceptadas: int
    filas_rechazadas: int


class ItineraryPreviewDay(BaseModel):
    """Cuántos arribos y salidas trae el archivo para un día."""

    fecha: date
    arribos: int
    salidas: int


class ItinerarySheet(BaseModel):
    """Una hoja del libro que podría leerse como itinerario.

    El archivo de la DGAC trae varias hojas parciales, así que la vista previa
    muestra cuál se leyó y qué otras había, con el rango de fechas de cada
    una, para que el operador pueda cambiar de hoja si eligió mal."""

    nombre: str
    filas: int
    fecha_min: date | None = None
    fecha_max: date | None = None
    elegida: bool = False


class ItineraryPreview(BaseModel):
    """Qué pasaría si se aplicara esta carga, calculado SIN tocar la base.

    Es lo que el operador revisa antes de confirmar: con qué formato se leyó
    el archivo, qué fechas cubre y cuánto itinerario ya cargado se reemplaza.
    La carga no modifica vuelos de la grilla, así que no hay nada que anticipar
    sobre ellos más allá de cuáles quedan sin itinerario."""

    # De qué aeródromo se va a reemplazar el itinerario. Va en la vista
    # previa y no solo en el formulario porque es el dato que vuelve
    # destructiva a la carga: con alcance "desde", elegir mal el aeródromo
    # borra el itinerario de otro de esa fecha en adelante.
    estacion: str
    formato: str  # "season" | "legacy" | "csv"
    # Hoja del libro que se leyó y hojas alternativas (solo en "season").
    hoja: str | None = None
    hojas: list[ItinerarySheet] = []
    alcance: str  # "dia" | "desde"
    filas_aceptadas: int
    filas_rechazadas: int
    errores: list[ItineraryRowError]
    # Rango de fechas que quedaría cargado. En 'legacy'/'csv' es siempre un
    # solo día (el 'vigente desde'), porque el archivo no trae fecha propia;
    # en 'season' depende de `alcance`.
    fecha_min: date | None = None
    fecha_max: date | None = None
    por_fecha: list[ItineraryPreviewDay]
    # Lo que hay hoy en la base desde 'vigente desde' en adelante y que esta
    # carga borra para reemplazarlo.
    filas_reemplazadas: int
    dias_reemplazados: int
    # Vuelos ya cargados en la grilla que dejan de tener itinerario con este
    # archivo. Es informativo: la carga no los toca. Sirve para que el operador
    # sepa qué revisar a mano si alguno de esos vuelos de verdad no va a operar.
    vuelos_sin_itinerario: list[str]
    # Avisos en castellano de lo que suele salir mal (archivo del día
    # equivocado, planilla que no es un itinerario, carga que vaciaría todo).
    advertencias: list[str]


class FlightHistoryImportRowError(BaseModel):
    row: int
    motivo: str


class FlightHistoryImportReport(BaseModel):
    filas_aceptadas: int
    filas_rechazadas: int
    # Cuántos vuelos se borraron para dejar lugar a esta carga. Se informa
    # aparte de las filas aceptadas porque son dos hechos distintos: lo que
    # entró y lo que se fue. Cero en una carga sobre un día vacío.
    vuelos_reemplazados: int = 0
    errores: list[FlightHistoryImportRowError]


class FlightHistoryPreviewRow(BaseModel):
    """Una fila parseada del CSV histórico, tal como quedaría en la grilla."""

    vuelo: str
    hora: str | None = None
    adep: str | None = None
    dep: str | None = None
    eta_aircon: str | None = None
    eta_aircon_calculado: str | None = None
    slot_arr_dgac: str | None = None
    etd1: str | None = None
    ctot1: str | None = None
    sec1: str | None = None
    h_rev1: str | None = None
    rev1: str | None = None
    etd2: str | None = None
    ctot2: str | None = None
    sec2: str | None = None
    etd3: str | None = None
    ctot3: str | None = None
    sec3: str | None = None
    etd4: str | None = None
    ctot4: str | None = None
    sec4: str | None = None
    etd5: str | None = None
    ctot5: str | None = None
    sec5: str | None = None
    dla_minutos: float | None = None
    observaciones: str | None = None
    cancelado: bool = False


class FlightHistoryPreview(BaseModel):
    """Resultado de la vista previa: qué se importaría, sin guardar nada."""

    filas_aceptadas: int
    filas_rechazadas: int
    errores: list[FlightHistoryImportRowError]
    rows: list[FlightHistoryPreviewRow]
    # Cuántos vuelos YA existen para ese sector+fecha (si > 0, el import
    # real va a ser rechazado con 409 -- se avisa desde la vista previa).
    vuelos_existentes: int


class FlightHistoryImportOut(BaseModel):
    """Una carga de grilla histórica. Mismo criterio que ItineraryUploadOut:
    los nombres de la API se conservan y salen de `integracion.importacion`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sector: Sector
    flight_date: date = Field(validation_alias="fecha_operacion")
    uploaded_at: datetime = Field(validation_alias="cargado_en")
    uploaded_by: str | None = Field(default=None, validation_alias="cargado_por")
    filename: str | None = Field(default=None, validation_alias="nombre_archivo")
    filas_aceptadas: int
    filas_rechazadas: int


class ForecastBucket(BaseModel):
    rango_hora: str
    pronosticado: int
    # Subconjunto de `pronosticado`: arribos con origen nacional (OACI SP..).
    pronosticado_llegadas_nacionales: int
    # Subconjunto de las llegadas nacionales: arribos que vienen de Cusco.
    pronosticado_llegadas_cusco: int
    # Las nacionales que no son de Cusco: el complemento exacto, para apilar.
    pronosticado_llegadas_nacionales_otras: int
    # Subconjunto de `pronosticado`: los despegues del itinerario.
    pronosticado_despegues: int
    trabajado: int
    capacidad_maxima: int


class ForecastResponse(BaseModel):
    sector: Sector | None = None
    fecha: date
    buckets: list[ForecastBucket]
