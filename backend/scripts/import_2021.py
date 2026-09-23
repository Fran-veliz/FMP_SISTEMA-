"""Carga los históricos de 2021, que vienen en un formato DISTINTO al
CONSOLIDADO de 2023+:

  - Dos carpetas separadas por sector: '... FMP LIMA NORTE' y '... FMP LIMA SUR'.
  - Adentro, subcarpetas por mes ('2021.10 OCT', '2021.11 NOV', '2021.12 DIC').
  - Un Excel POR DÍA Y POR SECTOR, ej:
      '01 OCTUBRE 2021 1200 2359 FMP LIMA NOR ARR.xlsx'
  - Cada Excel trae 2 hojas: 'INFO' (itinerario del día, idéntico en el archivo
    NOR y en el SUR) y 'FormatoFMP' (la grilla del sector, una sola).

Diferencias de la grilla 2021 respecto a 2023+ (el parser las tolera solo):
  - No existe 'ETA AIRCON CALCULADO'  -> eta_aircon_calculado queda vacío.
  - Usa 'DEM1..DEM4' (demora) en vez de 'SEC1..SEC4' (motivo) -> sec* queda
    vacío a propósito (son conceptos distintos; mejor vacío que mal mapeado).

La fecha sale del nombre del archivo (día + mes en español) y el sector de la
carpeta. El itinerario se carga UNA sola vez por día (del archivo que aparezca
primero); la restricción uq_itinerary_dedup igual evitaría duplicados.

Modos (dry-run por defecto, NO escribe):
  python -m scripts.import_2021 "<raiz que contiene NORTE y SUR>"
  python -m scripts.import_2021 "<raiz>" --commit
"""
from __future__ import annotations

import argparse
import datetime
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.flight_history_import import (  # noqa: E402
    buscar_hoja,
    hoja_a_csv,
    parse_flight_history_csv,
)
from app.services.texto import normalizar  # noqa: E402
from scripts.import_historico import (  # noqa: E402
    DayResult,
    FileInfo,
    _db_session,
    _mes_dia,
    _year_from_path,
    cargar_dia,
    parse_info_itinerary,
)


def _sector_de(path: Path) -> str | None:
    """NOR o SUR según la carpeta/nombre. 'NORTE' antes que 'SUR' para no
    confundir (NORTE no contiene 'SUR')."""
    p = normalizar(str(path))
    if "NORTE" in p or "LIMA NOR" in p:
        return "NOR"
    if "SUR" in p:
        return "SUR"
    return None


def _fecha_de(path: Path) -> datetime.date | None:
    md = _mes_dia(normalizar(path.name))
    anio = _year_from_path(path)
    if not md or not anio:
        return None
    mes, dia = md
    try:
        return datetime.date(anio, mes, dia)
    except ValueError:
        return None


def descubrir(raiz: Path) -> dict[datetime.date, dict[str, Path]]:
    """fecha -> {sector: archivo}."""
    dias: dict[datetime.date, dict[str, Path]] = defaultdict(dict)
    for p in sorted(raiz.rglob("*.xlsx")):
        if p.name.startswith("~$"):
            continue
        sector = _sector_de(p)
        fecha = _fecha_de(p)
        if sector is None or fecha is None:
            print(f"  ⚠ se saltea (sector/fecha ilegible): {p.name}")
            continue
        if sector in dias[fecha]:
            print(f"  ⚠ {fecha} {sector}: ya había un archivo, se ignora el repetido {p.name}")
            continue
        dias[fecha][sector] = p
    return dias


def construir_dia(fecha: datetime.date, por_sector: dict[str, Path]) -> DayResult:
    res = DayResult(info=FileInfo(next(iter(por_sector.values())), fecha, None, None),
                    grid_rows={}, itin=[])
    itin_cargado = False
    for sector, path in sorted(por_sector.items()):
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        grid = buscar_hoja(wb, "FormatoFMP")
        if grid is None:
            res.avisos.append(f"{sector}: sin hoja FormatoFMP, no se carga ese sector")
        else:
            parsed = parse_flight_history_csv(hoja_a_csv(wb[grid]))
            res.grid_rows[sector] = parsed.rows
            if sector == "NOR":
                res.nor = parsed.filas_aceptadas
            else:
                res.sur = parsed.filas_aceptadas
            if parsed.filas_rechazadas:
                res.avisos.append(f"{sector}: {parsed.filas_rechazadas} fila(s) descartada(s)")
        # El itinerario (INFO) es igual en NOR y SUR: se toma una sola vez.
        if not itin_cargado:
            info = buscar_hoja(wb, "INFO")
            if info is not None:
                res.itin = parse_info_itinerary(wb[info])
                res.itin_arr = sum(1 for e in res.itin if e.direction == "ARR")
                res.itin_dep = sum(1 for e in res.itin if e.direction == "DEP")
                itin_cargado = True
    if res.nor == 0 and res.sur == 0:
        res.fatales.append("no se pudo leer ninguna grilla")
    return res


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Carga de históricos 2021 (formato separado NORTE/SUR).")
    ap.add_argument("raiz", type=Path, help="Carpeta que contiene '... FMP LIMA NORTE' y '... FMP LIMA SUR'")
    ap.add_argument("--commit", action="store_true", help="Escribe a la base (default: dry-run).")
    args = ap.parse_args()

    if not args.raiz.exists():
        sys.exit(f"No existe la carpeta: {args.raiz}")

    dias = descubrir(args.raiz)
    print(f"\nDías encontrados: {len(dias)}  (rango {min(dias)} … {max(dias)})")

    por_mes = defaultdict(lambda: [0, 0, 0])  # dias, nor_files, sur_files
    for fecha, ps in sorted(dias.items()):
        k = fecha.strftime("%Y-%m")
        por_mes[k][0] += 1
        por_mes[k][1] += 1 if "NOR" in ps else 0
        por_mes[k][2] += 1 if "SUR" in ps else 0
    print(f"\n{'MES':<9}{'DÍAS':>6}{'NOR':>6}{'SUR':>6}")
    for k in sorted(por_mes):
        d, n, s = por_mes[k]
        print(f"{k:<9}{d:>6}{n:>6}{s:>6}")

    if not args.commit:
        print("\n[DRY-RUN] No se escribió nada. Repetí con --commit para cargar.")
        return

    from sqlalchemy import func, select

    from app.models import Flight

    db = _db_session()
    cargados = 0
    try:
        for fecha, ps in sorted(dias.items()):
            existe = db.execute(
                select(func.count()).select_from(Flight).where(Flight.flight_date == fecha)
            ).scalar()
            if existe:
                print(f"  · {fecha}: ya tiene {existe} vuelos, se saltea.")
                continue
            res = construir_dia(fecha, ps)
            if res.fatales:
                print(f"  ⛔ {fecha}: {res.fatales[0]}")
                continue
            try:
                cargar_dia(db, res)
                db.commit()
                cargados += 1
                print(f"  ✔ {fecha}: NOR={res.nor} SUR={res.sur} itin={len(res.itin)}"
                      + (f"  ⚠ {'; '.join(res.avisos)}" if res.avisos else ""))
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                print(f"  ⚠ {fecha}: NO se cargó — {exc}")
            finally:
                db.expunge_all()
        print(f"\n✔ Listo: {cargados} día(s) cargado(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
