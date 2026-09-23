"""Carga masiva de vuelos históricos + itinerario desde las carpetas de años.

Estructura de origen esperada (una carpeta por año, adentro una por mes, y
adentro un Excel "CONSOLIDADO" por día)::

    <raiz>/2023/2023.06 JUN NOR-SUR/JUNIO 3 CONSOLIDADO.xlsx

Cada Excel diario trae:
  - hoja 'FormatoFMP NOR'  -> grilla del sector NOR  (tabla flights)
  - hoja 'FormatoFMP SUR'  -> grilla del sector SUR  (tabla flights)
  - hoja 'INFO'            -> itinerario del día      (tabla itinerary_entries)

La FECHA de cada día sale del NOMBRE del archivo (mes en español + día) y del
AÑO de la carpeta; se cruza con la celda FECHA interna del Excel y, si no
coinciden, el archivo se SALTA y se reporta (no se carga a medias).

Modos (por seguridad, dry-run es el default: NO escribe nada):
  python -m scripts.import_historico <raiz>                 # dry-run: solo reporte
  python -m scripts.import_historico <raiz> --delete-historical   # borra SOLO los históricos ya cargados
  python -m scripts.import_historico <raiz> --commit        # carga a la base

La reutiliza el parser oficial de la grilla (app.services.flight_history_import) para no
duplicar la lógica de columnas.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import openpyxl

# Permite `python backend/scripts/import_historico.py` además de `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.flight_history_import import (  # noqa: E402
    ERRORES_EXCEL,
    InvalidFlightHistoryFile,
    buscar_hoja,
    campos_del_vuelo_historico,
    celda_a_texto,
    hoja_a_csv,
    parse_flight_history_csv,
)
from app.services.texto import normalizar  # noqa: E402

MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10,
    "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
# El nombre viene en dos órdenes según el mes: "JUNIO 3" (mes día) o
# "01 ENERO" (día mes). Se contemplan los dos.
_MES = "|".join(MESES)
# Espacio opcional y sin exigir separador de palabra, para tolerar nombres
# pegados ("JULIO12", "OCTUBRE 3CONSOLIDADO"). (?!\d) evita cortar un número
# de dos cifras; (?<!\d) evita enganchar parte de otro número.
_MES_DIA_RE = re.compile(r"(" + _MES + r")\s*(\d{1,2})(?!\d)")
_DIA_MES_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(" + _MES + r")")
_ANIO_RE = re.compile(r"(20\d{2})")


def _mes_dia(nombre_norm: str) -> tuple[int, int] | None:
    m = _MES_DIA_RE.search(nombre_norm)
    if m:
        return MESES[m.group(1)], int(m.group(2))
    m = _DIA_MES_RE.search(nombre_norm)
    if m:
        return MESES[m.group(2)], int(m.group(1))
    return None


# --------------------------------------------------------------------------- #
# Conversión de celdas
# --------------------------------------------------------------------------- #
# Valores de error de fórmula que el Excel viejo dejó cacheados en algunas
# celdas (DLA sobre todo). Equivalen a "sin dato": no deben rechazar la fila.
def _hhmm_time(v: object) -> str | None:
    """Hora del itinerario -> 'HHMM' (máx 4). Acepta datetime.time, el número
    serial de Excel (fracción de día: 0.9895… = 23:45) y textos 'HH:MM[:SS]'."""
    if v is None:
        return None
    if isinstance(v, (datetime.datetime, datetime.time)):
        return v.strftime("%H%M")
    if isinstance(v, float) and 0 <= v < 1:
        total = round(v * 1440)  # minutos desde 00:00
        return f"{(total // 60) % 24:02d}{total % 60:02d}"
    if isinstance(v, (int, float)):
        return str(int(v))[:4] or None
    s = str(v).strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        return f"{parts[0].zfill(2)}{parts[1].zfill(2)}"[:4]
    return s[:4]


# --------------------------------------------------------------------------- #
# Itinerario (hoja INFO)
# --------------------------------------------------------------------------- #
@dataclass
class ItinRow:
    direction: str  # "ARR" | "DEP"
    call_sign: str
    hora_utc: str | None
    aerodromo: str | None
    tipo_aeronave: str | None
    tipo_servicio: str | None


def parse_info_itinerary(ws) -> list[ItinRow]:
    """Extrae llegadas (bloque izquierdo) y salidas (bloque derecho) de INFO.

    Las columnas se ubican por encabezado (dos 'CALL SIGN' lado a lado), no por
    índice fijo, para aguantar cambios de espaciado entre años.
    """
    rows = list(ws.iter_rows(values_only=True))
    hdr_idx = None
    for i, r in enumerate(rows[:12]):
        if any(normalizar(c) == "CALL SIGN" for c in r):
            hdr_idx = i
            break
    if hdr_idx is None:
        return []

    hdr = rows[hdr_idx]
    normed = [normalizar(c) for c in hdr]
    cs_cols = [i for i, v in enumerate(normed) if v == "CALL SIGN"]

    def find_in_range(start: int, end: int, *cands: str) -> int | None:
        targets = {normalizar(c) for c in cands}
        for j in range(start, min(end, len(hdr))):
            if normed[j] in targets:
                return j
        return None

    blocks: list[tuple[str, int, int]] = []
    for k, cs in enumerate(cs_cols):
        end = cs_cols[k + 1] if k + 1 < len(cs_cols) else len(hdr)
        blocks.append(("ARR" if k == 0 else "DEP", cs, end))

    def cell(r, idx):
        v = r[idx] if idx is not None and idx < len(r) else None
        return None if str(v).strip().upper() in ERRORES_EXCEL else v

    entries: list[ItinRow] = []
    for direction, cs, end in blocks[:2]:
        col_hora = find_in_range(cs, end, "HORA ARR UTC", "HORA DEP UTC", "HORA")
        col_aero = find_in_range(cs, end, "ORIGEN", "DESTINO")
        col_ta = find_in_range(cs, end, "TIPO DE AERONAVE", "TIPO AERONAVE")
        col_ts = find_in_range(cs, end, "TIPO DE SERVICIO", "TIPO SERVICIO")
        for r in rows[hdr_idx + 1:]:
            raw = cell(r, cs)
            call = str(raw).strip().upper() if raw else None
            if not call:
                continue
            entries.append(
                ItinRow(
                    direction=direction,
                    call_sign=call[:16],
                    hora_utc=(_hhmm_time(cell(r, col_hora)) or None),
                    aerodromo=(str(cell(r, col_aero)).strip()[:8] or None) if cell(r, col_aero) else None,
                    tipo_aeronave=(str(cell(r, col_ta)).strip()[:8] or None) if cell(r, col_ta) else None,
                    tipo_servicio=(str(cell(r, col_ts)).strip()[:4] or None) if cell(r, col_ts) else None,
                )
            )
    return entries


# --------------------------------------------------------------------------- #
# Fecha del archivo
# --------------------------------------------------------------------------- #
def _year_from_path(path: Path) -> int | None:
    for part in reversed(path.parts):
        m = _ANIO_RE.search(part)
        if m:
            return int(m.group(1))
    return None


def _fecha_interna(wb) -> datetime.date | None:
    """Busca la celda FECHA en las primeras filas de FormatoFMP SUR/NOR."""
    for sheet in ("FormatoFMP SUR", "FormatoFMP NOR"):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        for row in ws.iter_rows(min_row=1, max_row=4, values_only=True):
            for j, c in enumerate(row):
                if normalizar(c) == "FECHA":
                    for nxt in row[j + 1:]:
                        if isinstance(nxt, datetime.datetime):
                            return nxt.date()
                        if isinstance(nxt, datetime.date):
                            return nxt
    return None


@dataclass
class FileInfo:
    path: Path
    fecha: datetime.date | None
    fecha_interna: datetime.date | None
    problema: str | None = None


def resolver_fecha(path: Path, wb) -> FileInfo:
    md = _mes_dia(normalizar(path.name))
    anio = _year_from_path(path)
    fecha = None
    if md and anio:
        mes, dia = md
        try:
            fecha = datetime.date(anio, mes, dia)
        except ValueError:
            return FileInfo(path, None, None, f"fecha inválida ({anio}-{mes}-{dia})")
    else:
        falta = "mes/día en el nombre" if not md else "año en la carpeta"
        return FileInfo(path, None, None, f"no se pudo leer la fecha ({falta})")

    interna = _fecha_interna(wb)
    problema = None
    if interna and interna != fecha:
        problema = f"la celda FECHA dice {interna} pero el nombre dice {fecha}"
    return FileInfo(path, fecha, interna, problema)


# --------------------------------------------------------------------------- #
# Procesamiento de un archivo
# --------------------------------------------------------------------------- #
@dataclass
class DayResult:
    info: FileInfo
    nor: int = 0
    sur: int = 0
    itin_arr: int = 0
    itin_dep: int = 0
    fatales: list[str] = None   # impiden cargar el día
    avisos: list[str] = None    # el día se carga igual, pero se informa
    grid_rows: dict = None      # sector -> list[FlightHistoryRow]   (solo en commit)
    itin: list = None           # list[ItinRow]                      (solo en commit)

    def __post_init__(self):
        if self.fatales is None:
            self.fatales = []
        if self.avisos is None:
            self.avisos = []

    @property
    def cargable(self) -> bool:
        return not self.fatales and self.info.fecha is not None


def procesar_archivo(path: Path, cargar_datos: bool) -> DayResult:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    info = resolver_fecha(path, wb)
    res = DayResult(info=info, grid_rows={}, itin=[])
    if info.problema:
        # Sin fecha confiable no se puede cargar: es fatal.
        res.fatales.append(info.problema)
        return res

    for sector, prefijo in (("NOR", "FormatoFMP NOR"), ("SUR", "FormatoFMP SUR")):
        sheet = buscar_hoja(wb, prefijo)
        if sheet is None:
            res.avisos.append(f"falta la hoja {prefijo!r}: no se carga ese sector")
            continue
        try:
            parsed = parse_flight_history_csv(hoja_a_csv(wb[sheet]))
        except InvalidFlightHistoryFile as exc:
            res.avisos.append(f"{sheet}: {exc}: no se carga ese sector")
            continue
        if parsed.filas_rechazadas:
            res.avisos.append(
                f"{sheet}: {parsed.filas_rechazadas} fila(s) descartada(s) "
                f"(ej: {parsed.errores[0][1]})"
            )
        if sector == "NOR":
            res.nor = parsed.filas_aceptadas
        else:
            res.sur = parsed.filas_aceptadas
        if cargar_datos:
            res.grid_rows[sector] = parsed.rows

    if res.nor == 0 and res.sur == 0:
        res.fatales.append("no se pudo leer ninguna grilla (NOR ni SUR)")

    info_sheet = buscar_hoja(wb, "INFO")
    if info_sheet is not None:
        itin = parse_info_itinerary(wb[info_sheet])
        res.itin_arr = sum(1 for e in itin if e.direction == "ARR")
        res.itin_dep = sum(1 for e in itin if e.direction == "DEP")
        if cargar_datos:
            res.itin = itin
    else:
        res.avisos.append("falta la hoja 'INFO': se carga sin itinerario")

    return res


def marcar_duplicados(resultados: list[DayResult]) -> None:
    """Si dos archivos apuntan al mismo día, se queda el primero y el resto se
    marca como fatal (para no cargar el día dos veces)."""
    visto: dict[datetime.date, str] = {}
    for r in resultados:
        if not r.cargable:
            continue
        prev = visto.get(r.info.fecha)
        if prev is None:
            visto[r.info.fecha] = r.info.path.name
        else:
            r.fatales.append(f"día duplicado: {r.info.fecha} ya lo cubre {prev!r}")


def descubrir_archivos(raiz: Path) -> list[Path]:
    return sorted(
        p for p in raiz.rglob("*.xlsx")
        if not p.name.startswith("~$") and "CONSOLIDADO" in normalizar(p.name)
    )


# --------------------------------------------------------------------------- #
# Base de datos (solo en --commit / --delete-historical)
# --------------------------------------------------------------------------- #
def _db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = os.environ.get("DATABASE_URL")
    if not url:
        # Fallback: base en Docker expuesta en el host.
        env = _leer_dotenv(Path(__file__).resolve().parents[2] / ".env")
        user = env.get("POSTGRES_USER", "ctot")
        pwd = env.get("POSTGRES_PASSWORD", "ctot")
        db = env.get("POSTGRES_DB", "ctot")
        port = env.get("DB_PORT", "5433")
        url = f"postgresql+psycopg://{user}:{pwd}@127.0.0.1:{port}/{db}"
    engine = create_engine(url, pool_pre_ping=True)
    return sessionmaker(bind=engine)()


def _leer_dotenv(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def borrar_historicos(db) -> None:
    """Borra SOLO los vuelos que entraron como históricos (los que tienen un
    registro en flight_history_imports), su itinerario y el registro mismo."""
    from sqlalchemy import delete, select

    from app import clock
    from app.models import Flight, Importacion, ItineraryEntry, TipoImportacion
    from app.services import itinerario

    pares = db.execute(
        select(Importacion.sector, Importacion.fecha_operacion).where(
            Importacion.tipo == TipoImportacion.GRILLA_HISTORICA
        )
    ).all()
    if not pares:
        print("No hay históricos registrados para borrar.")
        return

    fechas = {fd for _, fd in pares}
    borrados = 0
    for sector, fd in pares:
        borrados += db.execute(
            delete(Flight).where(Flight.sector == sector, Flight.flight_date == fd)
        ).rowcount or 0
    # Acotado al aeródromo, igual que el resto de las escrituras sobre esta
    # tabla: deshacer una carga histórica de Lima no puede llevarse el
    # itinerario de Cusco de esas mismas fechas.
    itin = db.execute(
        delete(ItineraryEntry).where(
            ItineraryEntry.estacion == itinerario.ESTACION_POR_DEFECTO,
            ItineraryEntry.flight_date.in_(fechas),
        )
    ).rowcount or 0
    db.execute(
        delete(Importacion).where(Importacion.tipo == TipoImportacion.GRILLA_HISTORICA)
    )
    db.commit()
    print(f"Borrados {borrados} vuelos y {itin} filas de itinerario "
          f"de {len(fechas)} día(s) histórico(s).")


def cargar_dia(db, res: DayResult, solo_vuelos: bool = False) -> None:
    """Carga un día. Con `solo_vuelos` no toca `itinerary_entries`.

    Hace falta cuando el itinerario de esas fechas YA está en la base (cargado
    en su momento por la subida diaria): las filas del INFO del Excel son las
    mismas y chocan contra uq_itinerary_dedup, lo que aborta el día entero y
    deja también los vuelos afuera."""
    from app import clock
    from app.models import Direction, Flight, Importacion, ItineraryEntry, Sector, TipoImportacion
    from app.services import itinerario

    now = clock.now_utc()
    fd = res.info.fecha

    for sector_str, rows in res.grid_rows.items():
        sector = Sector[sector_str]
        # Se registra la ejecucion primero y se vuelca para tener su id: asi
        # cada vuelo queda apuntando a la carga que lo creo, y deshacerla es un
        # DELETE por identificador en vez de borrar por sector y fecha.
        importacion = Importacion(
            tipo=TipoImportacion.GRILLA_HISTORICA,
            sector=sector, fecha_operacion=fd, cargado_en=now,
            cargado_por="IMPORT HISTÓRICO", nombre_archivo=res.info.path.name,
            filas_aceptadas=len(rows), filas_rechazadas=0,
        )
        db.add(importacion)
        db.flush()
        for numero, row in enumerate(rows, start=1):
            db.add(Flight(
                **campos_del_vuelo_historico(row),
                sector=sector, flight_date=fd, numero_fila=numero,
                importacion_id=importacion.id, fila_origen=numero,
                updated_at=now, updated_by="IMPORT HISTÓRICO",
            ))

    # Algunos INFO de origen traían el mismo vuelo repetido decenas de veces
    # (bloque pegado varias veces en el Excel). Se deduplica acá con la misma
    # clave que la restricción única uq_itinerary_dedup, así el import no choca
    # ni vuelve a inflar la tabla.
    if solo_vuelos:
        return

    vistos: set[tuple] = set()
    for e in res.itin:
        clave = (itinerario.ESTACION_POR_DEFECTO, e.call_sign, Direction[e.direction],
                 e.hora_utc, e.aerodromo, e.tipo_aeronave, e.tipo_servicio)
        if clave in vistos:
            continue
        vistos.add(clave)
        db.add(ItineraryEntry(
            # Todo el histórico es de Lima: el sistema fue mono-aeródromo hasta
            # 2026. Sin esto el INSERT viola la restricción NOT NULL y el
            # commit se lleva por delante también los vuelos del día, que van
            # en la misma transacción.
            estacion=itinerario.ESTACION_POR_DEFECTO,
            flight_date=fd, effective_from=fd, call_sign=e.call_sign,
            direction=Direction[e.direction], hora_utc=e.hora_utc, aerodromo=e.aerodromo,
            tipo_aeronave=e.tipo_aeronave, tipo_servicio=e.tipo_servicio, asientos=None,
        ))


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #
def imprimir_reporte(resultados: list[DayResult]) -> tuple[int, int]:
    ok = [r for r in resultados if r.cargable]
    fatales = [r for r in resultados if not r.cargable]
    con_avisos = [r for r in ok if r.avisos]

    por_mes = defaultdict(lambda: [0, 0, 0, 0, 0])  # dias, nor, sur, arr, dep
    for r in ok:
        k = r.info.fecha.strftime("%Y-%m")
        por_mes[k][0] += 1
        por_mes[k][1] += r.nor
        por_mes[k][2] += r.sur
        por_mes[k][3] += r.itin_arr
        por_mes[k][4] += r.itin_dep

    print("\n" + "=" * 66)
    print(f"{'MES':<9}{'DÍAS':>6}{'NOR':>8}{'SUR':>8}{'ITIN ARR':>11}{'ITIN DEP':>11}")
    print("-" * 66)
    tot = [0, 0, 0, 0, 0]
    for k in sorted(por_mes):
        d, n, s, a, dp = por_mes[k]
        print(f"{k:<9}{d:>6}{n:>8}{s:>8}{a:>11}{dp:>11}")
        for i, v in enumerate((d, n, s, a, dp)):
            tot[i] += v
    print("-" * 66)
    print(f"{'TOTAL':<9}{tot[0]:>6}{tot[1]:>8}{tot[2]:>8}{tot[3]:>11}{tot[4]:>11}")
    print("=" * 66)

    if fatales:
        print(f"\n⛔ {len(fatales)} archivo(s) NO se cargarán (revisá a mano):")
        for r in fatales:
            print(f"  • {r.info.path.name}")
            for e in r.fatales[:6]:
                print(f"      - {e}")
    else:
        print("\n✔  Todos los archivos con fecha válida se pueden cargar.")

    if con_avisos:
        print(f"\n⚠  {len(con_avisos)} día(s) se cargan igual pero con avisos:")
        for r in con_avisos:
            print(f"  • {r.info.fecha}  {r.info.path.name}")
            for e in r.avisos[:6]:
                print(f"      - {e}")

    return len(ok), len(fatales)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    # La consola de Windows usa cp1252 por defecto y revienta con los emojis
    # del reporte; forzamos UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Carga masiva de históricos + itinerario.")
    ap.add_argument("raiz", type=Path, help="Carpeta raíz con los años (ej: C:/Users/veliz/Downloads/2023)")
    ap.add_argument("--commit", action="store_true", help="Escribe los datos a la base (default: dry-run).")
    ap.add_argument("--delete-historical", action="store_true",
                    help="Borra SOLO los históricos ya cargados antes de nada.")
    ap.add_argument("--solo-vuelos", action="store_true",
                    help="No carga el itinerario (hoja INFO), solo las grillas NOR/SUR. "
                         "Usalo cuando el itinerario de esas fechas ya está en la base: "
                         "si no, las filas repetidas chocan y se pierde el día entero.")
    args = ap.parse_args()

    if not args.raiz.exists():
        sys.exit(f"No existe la carpeta: {args.raiz}")

    if args.delete_historical:
        print("Borrando históricos ya cargados…")
        db = _db_session()
        try:
            borrar_historicos(db)
        finally:
            db.close()
        if not args.commit:
            return

    archivos = descubrir_archivos(args.raiz)
    print(f"Encontrados {len(archivos)} archivo(s) CONSOLIDADO bajo {args.raiz}")
    if not archivos:
        return

    resultados = [procesar_archivo(p, cargar_datos=args.commit) for p in archivos]
    marcar_duplicados(resultados)
    n_ok, n_prob = imprimir_reporte(resultados)

    if not args.commit:
        print("\n[DRY-RUN] No se escribió nada. Revisá el reporte y, si está bien,")
        print("          volvé a correr con  --commit  (y --delete-historical si querés reemplazar).")
        return

    print(f"\nCargando {n_ok} día(s) a la base…")
    db = _db_session()
    try:
        from sqlalchemy import func, select
        from app.models import Flight

        cargables = [r for r in resultados if r.cargable]
        cargados = 0
        for i, r in enumerate(cargables, start=1):
            existe = db.execute(
                select(func.count()).select_from(Flight).where(Flight.flight_date == r.info.fecha)
            ).scalar()
            if existe:
                print(f"  · {r.info.fecha}: ya hay vuelos cargados, se saltea "
                      f"(borrá con --delete-historical si querés reemplazar).")
                continue
            try:
                cargar_dia(db, r, solo_vuelos=args.solo_vuelos)
                db.commit()      # commit por día: si algo falla, no se pierde lo ya cargado
                cargados += 1
            except Exception as exc:  # noqa: BLE001  un día roto no debe abortar todo
                db.rollback()
                print(f"  ⚠ {r.info.fecha} ({r.info.path.name}): NO se cargó — {exc}")
            finally:
                db.expunge_all()  # libera memoria entre días
            if i % 30 == 0 or i == len(cargables):
                print(f"  … {i}/{len(cargables)} días procesados")
        print(f"✔  Listo: {cargados} día(s) cargado(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
