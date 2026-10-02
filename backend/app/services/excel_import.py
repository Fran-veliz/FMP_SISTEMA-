"""Ingesta del itinerario diario (equivalente a la hoja 'INFO' del Excel).

Reglas duras para no arrastrar los problemas del workbook original:
  - Se abre con openpyxl(data_only=True): solo se leen valores YA calculados
    por Excel, nunca se reevalúan fórmulas ni referencias externas
    ('[1]Hoja'!celda), que en el archivo original quedan rotas apenas se
    mueve o renombra el libro.
  - Se valida la presencia de encabezados esperados antes de aceptar el
    archivo; si falta una columna crítica se rechaza con un mensaje claro
    en vez de importar datos a medias.
  - Cada fila se valida por separado: una fila con hora ilegible o sin call
    sign se reporta como error pero no aborta el resto del archivo.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time

import openpyxl

from app.services.ctot_calc import parse_hhmm
from app.services.texto import normalizar

REQUIRED_HEADERS_ARR = ["CALL SIGN", "HORA  ARR UTC", "ORIGEN", "TIPO DE AERONAVE", "TIPO DE SERVICIO"]
REQUIRED_HEADERS_DEP = ["CALL SIGN", "HORA  DEP UTC", "DESTINO", "TIPO DE AERONAVE", "TIPO DE SERVICIO"]


@dataclass
class ItineraryRow:
    call_sign: str
    direction: str  # "ARR" | "DEP"
    hora_utc: str | None  # HHMM
    aerodromo: str | None
    tipo_aeronave: str | None
    tipo_servicio: str | None
    flight_date: date | None = None  # solo lo trae el formato 'temporada' (una fecha por fila)
    asientos: int | None = None
    # Numero de fila del archivo de origen. Sirve para volver del dato cargado
    # al renglon que lo produjo: hasta ahora solo los errores decian de que
    # fila venian, y las filas aceptadas quedaban sin procedencia.
    fila_origen: int | None = None
    # Para devolver el archivo convertido en el mismo formato en que llegó:
    # la aerolínea y el número por separado, y los campos cuyo código no se
    # encontró en el catálogo ("aerolinea" / "tipo_aeronave" / "aerodromo").
    prefijo: str | None = None
    numero: str | None = None
    no_encontrados: list[str] = field(default_factory=list)


@dataclass
class HojaItinerario:
    """Una hoja del libro que tiene pinta de itinerario de temporada.

    El archivo que manda la DGAC es un libro de trabajo con varias hojas
    parciales (la convertida de la temporada pasada, la cruda "para
    convertir", la que arma el FMP). Se describen todas para que el operador
    vea cual se leyo y pueda elegir otra."""

    nombre: str
    fila_encabezado: int
    filas: int
    fecha_min: date | None = None
    fecha_max: date | None = None
    # Proporcion de filas de muestra cuyo numero de vuelo tiene pinta de call
    # sign OACI (SKX5368). En la hoja cruda de la DGAC el mismo campo trae
    # solo el numero ("5368"), y esa hoja no sirve para cargar.
    calidad: float = 0.0
    tiene_direccion: bool = False


@dataclass
class ImportResult:
    rows: list[ItineraryRow] = field(default_factory=list)
    errores: list[tuple[int, str]] = field(default_factory=list)  # (fila, motivo)
    hoja: str | None = None  # hoja del libro que se leyo (formato temporada)
    hojas: list[HojaItinerario] = field(default_factory=list)
    # "aerolinea" / "aerodromo" / "tipo_aeronave" -> qué se convirtió.
    conversiones: dict[str, ConversionCampo] = field(default_factory=dict)

    @property
    def filas_aceptadas(self) -> int:
        return len(self.rows)

    @property
    def filas_rechazadas(self) -> int:
        return len(self.errores)


class InvalidItineraryFile(Exception):
    pass


@dataclass
class Catalogo:
    """Equivalencias IATA -> OACI de un catálogo, y los OACI que ya conoce."""

    equivalencias: dict[str, str]
    oaci: set[str]


@dataclass
class CatalogosConversion:
    aerodromos: Catalogo
    aerolineas: Catalogo
    tipos_aeronave: Catalogo


@dataclass
class ConversionCampo:
    """Qué pasó con cada código de un campo al cargar el archivo.

    Por estado ("convertido", "sin_cambio", "no_encontrado") y por código:
    el resultado, en cuántas filas apareció, la primera fila del Excel y un
    ejemplo legible ("H8 5017 → SKX5017")."""

    detalle: dict[str, dict[str, dict]] = field(
        default_factory=lambda: {"convertido": {}, "sin_cambio": {}, "no_encontrado": {}}
    )

    def anotar(self, estado: str, codigo: str, resultado: str, fila: int, ejemplo: str) -> None:
        d = self.detalle[estado].setdefault(
            codigo, {"resultado": resultado, "filas": 0, "fila": fila, "ejemplo": ejemplo}
        )
        d["filas"] += 1

    def total(self, estado: str) -> int:
        return sum(d["filas"] for d in self.detalle[estado].values())


def _convertir(
    campo: ConversionCampo, catalogo: Catalogo, codigo: str, fila: int, ejemplo
) -> tuple[str, str]:
    """(código en OACI si el catálogo lo conoce, si no el mismo; estado)."""
    oaci = catalogo.equivalencias.get(codigo)
    if oaci and oaci != codigo:
        campo.anotar("convertido", codigo, oaci, fila, ejemplo(oaci))
        return oaci, "convertido"
    if oaci or codigo in catalogo.oaci:
        campo.anotar("sin_cambio", codigo, codigo, fila, ejemplo(codigo))
        return codigo, "sin_cambio"
    campo.anotar("no_encontrado", codigo, codigo, fila, ejemplo(codigo))
    return codigo, "no_encontrado"


def _texto_numero(valor: object) -> str:
    """El número de vuelo como texto: Excel guarda 5017 como número (y a
    veces como 5017.0); un texto se respeta tal cual, con sus ceros."""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _numero_tres_digitos(numero: str) -> str:
    """Completa con ceros hasta 3 dígitos: 25 -> 025, 5E -> 005E.

    Es como se escriben los indicativos en la grilla (AMP025, 468 veces en
    2026) y en los itinerarios de la DGAC. Sin esto, el archivo con la
    aerolínea aparte cargaba AMP25 y el vuelo que el operador anota como
    AMP025 no encontraba su itinerario. Los de 3 o más dígitos no cambian."""
    m = re.fullmatch(r"(\d+)(\D*)", numero)
    if m is None or len(m.group(1)) >= 3:
        return numero
    return m.group(1).zfill(3) + m.group(2)


def _hhmm_from_cell(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (time, datetime)):
        return f"{value.hour:02d}{value.minute:02d}"
    s = str(value).strip()
    if not s:
        return None
    # Segun como se haya armado la planilla, la misma columna viene como hora
    # de Excel o como texto ("00:05" / "00:05:00"). Se normaliza a HHMM, que es
    # lo que entiende parse_hhmm: sin esto, la hoja del itinerario de temporada
    # que usa el FMP se rechazaba fila por fila con "Hora ilegible".
    if ":" in s:
        partes = s.split(":")
        if len(partes) >= 2 and partes[0].strip().isdigit() and partes[1].strip().isdigit():
            return f"{int(partes[0]):02d}{int(partes[1]):02d}"
    return s


# Cuantas filas se miran, en cada hoja, buscando el encabezado. Los archivos
# de la DGAC no lo traen siempre en la fila 1: el libro de temporada abre con
# una fila de titulo ("ACTUALIZADO dd/mm/aaaa") y los recortes que arma el FMP
# quedan pegados mas abajo y corridos a la derecha.
MAX_FILAS_ENCABEZADO = 15

ALIAS_DIRECCION = ("SALIDA (D) LLEGADA (A)", "D / A", "D/A", "SALIDA / LLEGADA", "ARR/DES", "ARR / DES")
# El itinerario que llega con la aerolínea aparte ("PREFIJO VUELO" + "NUMERO
# VUELO") nombra distinto las mismas columnas que el libro de la DGAC.
ALIAS_VUELO = ("NUMERO DE VUELO", "NUMERO VUELO")
ALIAS_FECHA = ("FECHA UTC", "FECHA HORA UTC")
ALIAS_AERODROMO = ("ORIGEN / DESTINO", "ORIGEN /DESTINO", "ORIGEN/ DESTINO", "ORIGEN/DESTINO")
ALIAS_PREFIJO = ("PREFIJO VUELO", "PREFIJO DE VUELO")


def _find_col(headers: list[str], *nombres: str, start: int = 0) -> int | None:
    """Indice de la primera columna (desde `start`) que coincide con alguno de
    los alias, o None.

    Cuidado con el patron anterior `find_col(a) or find_col(b)`: la columna 0
    es falsy, asi que un archivo con el dato en la primera columna --el
    "D / A" de la hoja que usa el FMP-- se leia como columna ausente."""
    for nombre in nombres:
        target = normalizar(nombre)
        for idx in range(start, len(headers)):
            if headers[idx] == target:
                return idx
    return None


def _buscar_encabezado(wb, puntaje, preferidas: tuple[str, ...] = ()):
    """Busca en que hoja y en que fila esta el encabezado del itinerario.

    Antes se daba por sentado que era la fila 1 (o la 2) de la primera hoja, y
    por eso se rechazaban archivos validos: el libro de la DGAC trae siete
    hojas y la buena no es la primera.

    `puntaje(headers)` devuelve 0 si esa fila no es un encabezado, 1 si le
    falta alguna columna util y 2 si esta completo; se queda con el mejor, asi
    que entre la hoja de trabajo a medio armar y la definitiva gana la
    definitiva. Devuelve (hoja, nro_de_fila, headers) o None."""
    orden = [n for n in preferidas if n in wb.sheetnames]
    orden += [n for n in wb.sheetnames if n not in orden]

    mejor = None  # (puntaje, hoja, fila, headers)
    for name in orden:
        for i, row in enumerate(wb[name].iter_rows(values_only=True), 1):
            if i > MAX_FILAS_ENCABEZADO:
                break
            headers = [normalizar(v) for v in row]
            valor = puntaje(headers)
            if valor and (mejor is None or valor > mejor[0]):
                mejor = (valor, name, i, headers)
                if valor == 2:
                    break
        if mejor is not None and mejor[0] == 2:
            break

    return None if mejor is None else (mejor[1], mejor[2], mejor[3])


def _puntaje_legacy(headers: list[str]) -> int:
    if _find_col(headers, "CALL SIGN") is None:
        return 0
    return 2 if _find_col(headers, "HORA ARR UTC") is not None else 1


def _puntaje_season(headers: list[str]) -> int:
    if _find_col(headers, *ALIAS_VUELO) is None or _find_col(headers, *ALIAS_FECHA) is None:
        return 0
    return 2 if _find_col(headers, *ALIAS_DIRECCION) is not None else 1


def _saltar_hasta(ws, fila: int):
    """Iterador de filas posicionado justo despues del encabezado."""
    rows_iter = ws.iter_rows(values_only=True)
    for _ in range(fila):
        next(rows_iter, None)
    return rows_iter


def parse_itinerary_excel(content: bytes) -> ImportResult:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # archivo corrupto / no es xlsx
        raise InvalidItineraryFile(f"No se pudo abrir el archivo: {exc}") from exc

    hallazgo = _buscar_encabezado(wb, _puntaje_legacy, preferidas=("INFO",))
    if hallazgo is None:
        raise InvalidItineraryFile(
            "No se encontraron las columnas de arribos (CALL SIGN / HORA ARR UTC) "
            "en ninguna hoja del archivo. Revisa que tenga el mismo formato que la hoja INFO."
        )
    sheet_name, fila_encabezado, headers = hallazgo
    ws = wb[sheet_name]

    col_arr_call = _find_col(headers, "CALL SIGN")
    col_arr_hora = _find_col(headers, "HORA ARR UTC")
    col_arr_aer = _find_col(headers, "ORIGEN")
    col_arr_tipo = _find_col(headers, "TIPO DE AERONAVE")
    col_arr_serv = _find_col(headers, "TIPO DE SERVICIO")

    if col_arr_call is None or col_arr_hora is None:
        raise InvalidItineraryFile(
            "No se encontraron las columnas de arribos (CALL SIGN / HORA ARR UTC) "
            "en la hoja de itinerario. Revisa que el archivo tenga el mismo formato que la hoja INFO."
        )

    col_dep_call = _find_col(headers, "CALL SIGN", start=col_arr_call + 1)
    col_dep_hora = _find_col(headers, "HORA DEP UTC", start=col_arr_call + 1)
    col_dep_aer = _find_col(headers, "DESTINO", start=col_arr_call + 1)
    col_dep_tipo = _find_col(headers, "TIPO DE AERONAVE", start=col_arr_call + 1)
    col_dep_serv = _find_col(headers, "TIPO DE SERVICIO", start=col_arr_call + 1)

    referencia = _referencia_externa(
        content, sheet_name, fila_encabezado, [col_arr_call, col_arr_hora]
    )
    if referencia is not None:
        raise _error_formulas_externas(referencia)

    rows_iter = _saltar_hasta(ws, fila_encabezado)
    result = ImportResult()
    row_num = fila_encabezado  # el numero que se reporta es el de la fila real del Excel

    for raw_row in rows_iter:
        row_num += 1

        def cell(idx: int | None):
            if idx is None or idx >= len(raw_row):
                return None
            return raw_row[idx]

        call_sign = cell(col_arr_call)
        if call_sign not in (None, ""):
            hora = _hhmm_from_cell(cell(col_arr_hora))
            if hora is not None and parse_hhmm(hora) is None:
                result.errores.append((row_num, f"Hora de arribo ilegible: {hora!r}"))
            else:
                result.rows.append(
                    ItineraryRow(
                        call_sign=str(call_sign).strip(),
                        direction="ARR",
                        hora_utc=hora,
                        aerodromo=str(cell(col_arr_aer) or "").strip() or None,
                        tipo_aeronave=str(cell(col_arr_tipo) or "").strip() or None,
                        tipo_servicio=str(cell(col_arr_serv) or "").strip() or None,
                        fila_origen=row_num,
                    )
                )

        if col_dep_call is not None:
            call_sign_dep = cell(col_dep_call)
            if call_sign_dep not in (None, ""):
                hora = _hhmm_from_cell(cell(col_dep_hora))
                if hora is not None and parse_hhmm(hora) is None:
                    result.errores.append((row_num, f"Hora de despegue ilegible: {hora!r}"))
                else:
                    result.rows.append(
                        ItineraryRow(
                            call_sign=str(call_sign_dep).strip(),
                            direction="DEP",
                            hora_utc=hora,
                            aerodromo=str(cell(col_dep_aer) or "").strip() or None,
                            tipo_aeronave=str(cell(col_dep_tipo) or "").strip() or None,
                            tipo_servicio=str(cell(col_dep_serv) or "").strip() or None,
                            fila_origen=row_num,
                        )
                    )

    return result


def detect_itinerary_format(content: bytes) -> str:
    """'season' = itinerario completo de temporada (una fecha por fila, IATA).
    'legacy' = carga diaria estilo hoja INFO (dos bloques ARR/DEP, OACI).

    Se busca el encabezado en todas las hojas, y no solo en la fila 1 de la
    primera: el libro de la DGAC abre con una fila de titulo y con hojas
    auxiliares, asi que un itinerario de temporada perfectamente valido se
    tomaba como 'legacy' y terminaba rechazado."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception:
        return "legacy"
    return "season" if _buscar_encabezado(wb, _puntaje_season) else "legacy"


SEASON_DIRECTION_MAP = {"A": "ARR", "D": "DEP"}


MUESTRA_FORMULAS = 30  # filas que se miran buscando formulas a otro libro


def _es_formula_externa(texto: str) -> bool:
    """True para una formula que apunta a OTRO libro: '[1]Hoja'!celda.

    Se distingue de las referencias estructuradas de tabla (Tabla1[Columna])
    porque el corchete de un enlace externo encierra un numero."""
    i = texto.find("[")
    while i != -1:
        j = texto.find("]", i)
        if j > i + 1 and texto[i + 1 : j].isdigit():
            return True
        i = texto.find("[", i + 1)
    return False


def _tiene_enlaces_externos(content: bytes) -> bool:
    """Si el .xlsx no declara enlaces externos, no hace falta mirar formulas."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            return any(n.startswith("xl/externalLinks/") for n in z.namelist())
    except Exception:
        return False


def _referencia_externa(
    content: bytes, nombre_hoja: str, fila_encabezado: int, columnas: list
) -> str | None:
    """La primera formula a otro libro que aparezca en las columnas que
    importan (vuelo, fecha, hora), o None.

    Un archivo asi NO trae datos: lo que se ve en pantalla lo recalcula Excel
    al abrirlo, pero lo que queda guardado --lo unico que puede leer el
    sistema-- son los ultimos valores cacheados. Paso de verdad: un recorte
    "del 9 al 11 de setiembre" que por dentro guardaba el 1 al 4 de julio."""
    if not _tiene_enlaces_externos(content):
        return None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False, read_only=True)
    except Exception:
        return None
    if nombre_hoja not in wb.sheetnames:
        return None

    utiles = [c for c in columnas if c is not None]
    for n, raw in enumerate(_saltar_hasta(wb[nombre_hoja], fila_encabezado)):
        if n >= MUESTRA_FORMULAS:
            break
        for idx in utiles:
            if idx >= len(raw):
                continue
            valor = raw[idx]
            if isinstance(valor, str) and valor.startswith("=") and _es_formula_externa(valor):
                return valor
    return None


def _error_formulas_externas(referencia: str) -> InvalidItineraryFile:
    return InvalidItineraryFile(
        "El archivo no trae datos propios: sus celdas son fórmulas que apuntan a "
        f"otro libro ({referencia.lstrip('=')[:60]}). Excel muestra en pantalla lo "
        "que recalcula al abrirlo, pero lo que queda guardado --y lo único que "
        "puede leer el sistema-- son los últimos valores guardados, que pueden "
        "ser de otra fecha. Copiá la hoja y pegala como valores (Pegado especial "
        "→ Valores) y guardá, o subí directamente el libro original de la DGAC."
    )


MUESTRA_CALIDAD = 300  # filas que se miran para juzgar si la hoja sirve


def _pinta_de_call_sign(valor: object) -> bool:
    """"SKX5368" si; "5368" no. Sirve para distinguir la hoja ya convertida
    de la hoja cruda que la DGAC deja en el mismo libro."""
    texto = str(valor or "").strip().upper()
    return len(texto) >= 4 and texto[:3].isalpha() and any(c.isdigit() for c in texto[3:])


def _describir_hoja(ws, nombre: str, fila: int, headers: list[str]) -> HojaItinerario:
    """Recorre la hoja una vez para saber cuantos vuelos trae, que fechas
    cubre y si los numeros de vuelo tienen pinta de call sign."""
    col_vuelo = _find_col(headers, *ALIAS_VUELO)
    col_fecha = _find_col(headers, *ALIAS_FECHA)
    col_prefijo = _find_col(headers, *ALIAS_PREFIJO)
    filas = muestra = validos = 0
    fecha_min = fecha_max = None

    for raw in _saltar_hasta(ws, fila):
        if col_vuelo is None or col_vuelo >= len(raw) or raw[col_vuelo] in (None, ""):
            continue
        filas += 1
        if muestra < MUESTRA_CALIDAD:
            muestra += 1
            if col_prefijo is not None:
                # Con la aerolínea en su propia columna, el número solo no es
                # señal de hoja cruda: lo que cuenta es que traiga prefijo.
                prefijo = raw[col_prefijo] if col_prefijo < len(raw) else None
                validos += 1 if str(prefijo or "").strip() else 0
            else:
                validos += 1 if _pinta_de_call_sign(raw[col_vuelo]) else 0
        valor = raw[col_fecha] if col_fecha is not None and col_fecha < len(raw) else None
        fecha = valor.date() if isinstance(valor, datetime) else valor
        if isinstance(fecha, date):
            fecha_min = fecha if fecha_min is None or fecha < fecha_min else fecha_min
            fecha_max = fecha if fecha_max is None or fecha > fecha_max else fecha_max

    return HojaItinerario(
        nombre=nombre,
        fila_encabezado=fila,
        filas=filas,
        fecha_min=fecha_min,
        fecha_max=fecha_max,
        calidad=(validos / muestra) if muestra else 0.0,
        tiene_direccion=_find_col(headers, *ALIAS_DIRECCION) is not None,
    )


def _encabezado_season(ws) -> tuple[int, list[str]] | None:
    """(fila, encabezados) de la hoja, o None si no parece un itinerario."""
    for i, row in enumerate(ws.iter_rows(values_only=True), 1):
        if i > MAX_FILAS_ENCABEZADO:
            break
        headers = [normalizar(v) for v in row]
        if _puntaje_season(headers):
            return i, headers
    return None


def _hojas_season(wb) -> list[tuple[HojaItinerario, list[str]]]:
    """Todas las hojas del libro que parecen un itinerario de temporada, ya
    descriptas (filas, fechas, calidad). Recorrerlas enteras cuesta unos
    segundos en el libro de la DGAC, asi que solo se hace cuando hay que
    elegir: si el operador ya eligio la hoja, se va directo a esa."""
    encontradas = []
    for nombre in wb.sheetnames:
        ws = wb[nombre]
        hallado = _encabezado_season(ws)
        if hallado is not None:
            fila, headers = hallado
            encontradas.append((_describir_hoja(ws, nombre, fila, headers), headers))
    return encontradas


def _elegir_hoja(candidatas: list[HojaItinerario], min_date: date | None) -> HojaItinerario | None:
    """La mejor hoja para la fecha pedida: primero las que llegan hasta esa
    fecha, y entre esas la que tiene los datos mas sanos.

    Con el libro real de la DGAC esto descarta la hoja de la temporada
    anterior (termina en junio) y la hoja cruda sin convertir (numeros de
    vuelo sin aerolinea), que es justo la que se elegia sola al tomar
    siempre la primera hoja del libro."""
    utiles = [h for h in candidatas if h.filas]
    if min_date is not None:
        utiles = [h for h in utiles if h.fecha_max is not None and h.fecha_max >= min_date]
    if not utiles:
        return None
    # Se prefiere una hoja con columna de direccion, pero si ninguna la tiene
    # igual se devuelve la mejor: asi el rechazo explica que falta esa columna,
    # que es el problema real, en vez de "ninguna hoja cubre la fecha".
    con_direccion = [h for h in utiles if h.tiene_direccion]
    return sorted(
        con_direccion or utiles, key=lambda h: (round(h.calidad, 2), h.filas), reverse=True
    )[0]


def parse_itinerary_season_excel(
    content: bytes,
    iata_to_icao: dict[str, str],
    min_date: date | None = None,
    hoja: str | None = None,
    estacion: str = "SPJC",
    catalogos: CatalogosConversion | None = None,
) -> ImportResult:
    """Formato 'itinerario completo' (temporada, ej. hoja 'W25'): una fila
    por vuelo por día, con fecha propia (columna 'FECHA UTC') y aeródromo
    en código IATA (columna 'ORIGEN / DESTINO') que se convierte a OACI
    usando `iata_to_icao`. Si `min_date` se especifica, se descartan (sin
    contar como error) las filas anteriores a esa fecha -- así se puede
    subir el archivo completo de la temporada y solo tomar "de tal fecha
    en adelante".

    Si el archivo trae la aerolínea en su propia columna ("PREFIJO VUELO"),
    el indicativo se arma con ella y el número. Con `catalogos`, la
    aerolínea, el tipo de aeronave y el aeródromo se llevan de IATA a OACI y
    cada conversión queda anotada en `ImportResult.conversiones`; lo que no
    está en el catálogo entra como vino."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        raise InvalidItineraryFile(f"No se pudo abrir el archivo: {exc}") from exc

    candidatas: list[tuple[HojaItinerario, list[str]]] = []

    if hoja is not None:
        # Camino corto: la hoja ya la eligio el operador en la vista previa.
        hallado = _encabezado_season(wb[hoja]) if hoja in wb.sheetnames else None
        if hallado is None:
            raise InvalidItineraryFile(
                f"La hoja {hoja!r} no esta en el archivo o no tiene formato de "
                "itinerario. Hojas del archivo: "
                + ", ".join(repr(n) for n in wb.sheetnames)
            )
        fila_encabezado, headers = hallado
        nombre_hoja = hoja
    else:
        candidatas = _hojas_season(wb)
        if not candidatas:
            raise InvalidItineraryFile(
                "No se encontraron las columnas del itinerario de temporada "
                "(NUMERO DE VUELO / FECHA UTC / ORIGEN / DESTINO) en ninguna hoja "
                "del archivo."
            )
        elegida = _elegir_hoja([h for h, _ in candidatas], min_date)
        if elegida is None:
            # Antes de culpar a las fechas: si la hoja son formulas a otro
            # libro, las fechas que trae guardadas no son las que se ven en
            # pantalla, y ese es el problema de verdad.
            for h, hs in candidatas:
                ref = _referencia_externa(
                    content,
                    h.nombre,
                    h.fila_encabezado,
                    [_find_col(hs, *ALIAS_VUELO), _find_col(hs, *ALIAS_FECHA)],
                )
                if ref is not None:
                    raise _error_formulas_externas(ref)
            detalle = "; ".join(
                f"{h.nombre!r} va del {h.fecha_min} al {h.fecha_max}"
                for h, _ in candidatas
                if h.fecha_min is not None
            )
            raise InvalidItineraryFile(
                f"Ninguna hoja del archivo tiene vuelos desde el {min_date}. "
                + (f"Lo que trae el archivo: {detalle}." if detalle else "")
            )
        nombre_hoja = elegida.nombre
        fila_encabezado = elegida.fila_encabezado
        headers = dict((h.nombre, hs) for h, hs in candidatas)[nombre_hoja]

    ws = wb[nombre_hoja]
    rows_iter = _saltar_hasta(ws, fila_encabezado)

    col_vuelo = _find_col(headers, *ALIAS_VUELO)
    col_prefijo = _find_col(headers, *ALIAS_PREFIJO)
    col_dir = _find_col(headers, *ALIAS_DIRECCION)
    col_asientos = _find_col(headers, "ASIENTOS")
    col_tipo_ac = _find_col(headers, "TIPO DE AERONAVE")
    col_aerodromo = _find_col(headers, *ALIAS_AERODROMO)
    col_fecha = _find_col(headers, *ALIAS_FECHA)
    col_hora = _columna_hora_estacion(headers, estacion, iata_to_icao)
    col_serv = _find_col(headers, "TIPO DE SERVICIO")

    if col_vuelo is None or col_fecha is None or col_aerodromo is None:
        raise InvalidItineraryFile(
            "No se encontraron las columnas esperadas del itinerario de temporada "
            "(NÚMERO DE VUELO / FECHA UTC / ORIGEN / DESTINO)."
        )

    referencia = _referencia_externa(
        content, nombre_hoja, fila_encabezado, [col_vuelo, col_fecha, col_hora]
    )
    if referencia is not None:
        raise _error_formulas_externas(referencia)

    # Sin columna de direccion no hay forma de saber si la fila es arribo o
    # despegue: se corta aca con un mensaje claro, en vez de reportar
    # "direccion invalida" en cada una de las miles de filas del archivo.
    if col_dir is None:
        raise InvalidItineraryFile(
            'Al archivo le falta la columna de dirección ("D / A" o "SALIDA (D) / '
            'LLEGADA (A)"), así que no se puede saber si cada vuelo es arribo o '
            "despegue. Suele pasar cuando se copian solo algunas columnas del "
            "itinerario en una hoja nueva: cargue el archivo completo de la DGAC."
        )

    result = ImportResult(hoja=nombre_hoja, hojas=[h for h, _ in candidatas])
    row_num = fila_encabezado  # se reporta el numero de fila real del Excel

    for raw_row in rows_iter:
        row_num += 1

        def cell(idx: int | None):
            if idx is None or idx >= len(raw_row):
                return None
            return raw_row[idx]

        call_sign = cell(col_vuelo)
        if call_sign in (None, ""):
            continue

        fecha_val = cell(col_fecha)
        flight_date = fecha_val.date() if isinstance(fecha_val, datetime) else fecha_val
        if not isinstance(flight_date, date):
            result.errores.append((row_num, f"Fecha ilegible: {fecha_val!r}"))
            continue
        if min_date is not None and flight_date < min_date:
            continue

        direccion_raw = str(cell(col_dir) or "").strip().upper()
        direction = SEASON_DIRECTION_MAP.get(direccion_raw)
        if direction is None:
            result.errores.append((row_num, f"Dirección inválida (debe ser A/D): {direccion_raw!r}"))
            continue

        hora = _hhmm_from_cell(cell(col_hora))
        if hora is None or parse_hhmm(hora) is None:
            result.errores.append((row_num, f"Hora ilegible: {hora!r}"))
            continue

        iata = str(cell(col_aerodromo) or "").strip().upper()
        icao = iata_to_icao.get(iata) or iata or None
        tipo_ac = str(cell(col_tipo_ac) or "").strip() or None
        numero = _texto_numero(call_sign)
        prefijo = str(cell(col_prefijo) or "").strip().upper() if col_prefijo is not None else ""
        if prefijo:
            numero = _numero_tres_digitos(numero)
        call_sign_final = f"{prefijo}{numero}" if prefijo else numero
        no_encontrados: list[str] = []

        if catalogos is not None:
            conv = result.conversiones
            if prefijo:
                prefijo, estado = _convertir(
                    conv.setdefault("aerolinea", ConversionCampo()), catalogos.aerolineas,
                    prefijo, row_num, lambda r: f"{prefijo} {numero} → {r}{numero}",
                )
                call_sign_final = f"{prefijo}{numero}"
                if estado == "no_encontrado":
                    no_encontrados.append("aerolinea")
            if iata:
                icao, estado = _convertir(
                    conv.setdefault("aerodromo", ConversionCampo()), catalogos.aerodromos,
                    iata, row_num, lambda r: f"{iata} → {r}",
                )
                if estado == "no_encontrado":
                    no_encontrados.append("aerodromo")
            if tipo_ac:
                codigo_ac = tipo_ac.upper()
                tipo_ac, estado = _convertir(
                    conv.setdefault("tipo_aeronave", ConversionCampo()), catalogos.tipos_aeronave,
                    codigo_ac, row_num, lambda r: f"{codigo_ac} → {r}",
                )
                if estado == "no_encontrado":
                    no_encontrados.append("tipo_aeronave")

        asientos_val = cell(col_asientos)
        try:
            asientos = int(asientos_val) if asientos_val not in (None, "") else None
        except (TypeError, ValueError):
            asientos = None

        result.rows.append(
            ItineraryRow(
                call_sign=call_sign_final,
                direction=direction,
                hora_utc=hora,
                aerodromo=icao,
                tipo_aeronave=tipo_ac,
                tipo_servicio=str(cell(col_serv) or "").strip() or None,
                flight_date=flight_date,
                asientos=asientos,
                fila_origen=row_num,
                prefijo=prefijo or None,
                numero=numero,
                no_encontrados=no_encontrados,
            )
        )

    return result


_ENCABEZADOS_CONVERTIDO = [
    "PREFIJO VUELO", "NUMERO VUELO", "ARR/DES", "TIPO DE AERONAVE", "ORIGEN /DESTINO",
    "FECHA HORA UTC", "HORA UTC", "TIPO DE SERVICIO", "FILA ORIGINAL", "OBSERVACIONES",
]
_COLUMNA_DE_CAMPO = {"aerolinea": 1, "tipo_aeronave": 4, "aerodromo": 5}
_NOMBRE_DE_CAMPO = {"aerolinea": "aerolínea", "tipo_aeronave": "tipo de aeronave", "aerodromo": "origen/destino"}


def itinerario_convertido_xlsx(result: ImportResult) -> bytes:
    """El itinerario ya llevado a OACI, en el mismo formato de columnas que
    se sube, para corregirlo a mano y volver a cargarlo.

    Las celdas con un código que no está en el catálogo van en amarillo y la
    fila lo dice en OBSERVACIONES; "FILA ORIGINAL" es la del archivo subido.
    Al volver a subirlo, los códigos ya en OACI se reconocen como tales."""
    from openpyxl.styles import Font, PatternFill

    amarillo = PatternFill("solid", fgColor="FFFF00")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (result.hoja or "Itinerario")[:31]
    ws.append(_ENCABEZADOS_CONVERTIDO)
    for celda in ws[1]:
        celda.font = Font(bold=True)
    for row in result.rows:
        hora = row.hora_utc or ""
        ws.append([
            row.prefijo or "",
            row.numero if row.numero is not None else row.call_sign,
            "A" if row.direction == "ARR" else "D",
            row.tipo_aeronave or "",
            row.aerodromo or "",
            row.flight_date,
            f"{hora[:2]}:{hora[2:]}" if len(hora) == 4 else hora,
            row.tipo_servicio or "",
            row.fila_origen,
            "; ".join(f"{_NOMBRE_DE_CAMPO[c]} no encontrada en el catálogo" for c in row.no_encontrados),
        ])
        for campo in row.no_encontrados:
            ws.cell(row=ws.max_row, column=_COLUMNA_DE_CAMPO[campo]).fill = amarillo
        ws.cell(row=ws.max_row, column=6).number_format = "DD/MM/YYYY"
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _columna_hora_estacion(
    headers: list[str], estacion: str, iata_to_icao: dict[str, str]
) -> int:
    """La hora aprobada pertenece al aeropuerto que recibe la carga.

    Un encabezado genérico debe declarar UTC. Los encabezados que nombran
    aeropuerto se contrastan con el catálogo, incluyendo el formato antiguo
    «HORA APROBADA LIM» que ya utiliza el sistema.
    """
    estacion = estacion.strip().upper()
    codigos = {estacion} | {
        iata.strip().upper() for iata, oaci in iata_to_icao.items()
        if oaci and oaci.strip().upper() == estacion
    }
    columnas: list[int] = []
    for indice, encabezado in enumerate(headers):
        if encabezado in {"HORA UTC", "HORA APROBADA UTC"}:
            columnas.append(indice)
            continue
        explicito = re.fullmatch(r"HORA APROBADA ([A-Z]{3,4})(?: UTC)?", encabezado)
        if explicito:
            codigo = explicito.group(1)
            if codigo not in codigos:
                raise InvalidItineraryFile(
                    f"La columna {encabezado!r} corresponde a {codigo}, "
                    f"pero la carga está seleccionada para {estacion}. "
                    "Seleccione el aeropuerto correcto o revise el archivo."
                )
            columnas.append(indice)
    if not columnas:
        raise InvalidItineraryFile(
            f"Falta la columna de hora aprobada de {estacion}. Use "
            "HORA APROBADA <código IATA/OACI> UTC o HORA APROBADA UTC."
        )
    if len(columnas) != 1:
        raise InvalidItineraryFile(
            "Hay varias columnas de hora aprobada; deje una sola para evitar "
            "importar un horario ambiguo."
        )
    return columnas[0]


def parse_itinerary_csv(content: bytes) -> ImportResult:
    """Esquema plano alternativo: call_sign,direction,hora_utc,aerodromo,tipo_aeronave,tipo_servicio"""
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    required = {"call_sign", "direction", "hora_utc"}
    if reader.fieldnames is None or not required.issubset({f.strip().lower() for f in reader.fieldnames}):
        raise InvalidItineraryFile(
            f"El CSV debe tener al menos las columnas: {', '.join(sorted(required))}"
        )

    result = ImportResult()
    for row_num, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): v for k, v in raw.items()}
        call_sign = (row.get("call_sign") or "").strip()
        direction = (row.get("direction") or "").strip().upper()
        hora = (row.get("hora_utc") or "").strip() or None

        if not call_sign:
            result.errores.append((row_num, "Falta call sign"))
            continue
        if direction not in ("ARR", "DEP"):
            result.errores.append((row_num, f"direction inválido (debe ser ARR/DEP): {direction!r}"))
            continue
        if hora is not None and parse_hhmm(hora) is None:
            result.errores.append((row_num, f"Hora ilegible: {hora!r}"))
            continue

        result.rows.append(
            ItineraryRow(
                call_sign=call_sign,
                direction=direction,
                hora_utc=hora,
                aerodromo=(row.get("aerodromo") or "").strip() or None,
                tipo_aeronave=(row.get("tipo_aeronave") or "").strip() or None,
                tipo_servicio=(row.get("tipo_servicio") or "").strip() or None,
                fila_origen=row_num,
            )
        )
    return result
