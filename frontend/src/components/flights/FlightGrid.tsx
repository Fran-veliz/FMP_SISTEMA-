import { useEffect, useMemo, useState } from "react";
import { useAppContext } from "../../contexts/AppContext";
import { api, ApiError } from "../../services/api";
import { applyQuickFilter, computeQuickFilterCounts } from "../../utils/flightStatus";
import { horaHhmmUtc } from "../../utils/utcDate";
import type { QuickFilter } from "../../utils/flightStatus";
import type { Flight, ItineraryLookup, Sector } from "../../types";
import { FlightRow } from "./FlightRow";
import { HistoryModal } from "./HistoryModal";
import {
  ACTIONS_COL_WIDTH,
  COLUMNS,
  GROUP_LABELS,
  REV_TO_HREV,
  ROW_NUMBER_COL_WIDTH,
  groupHeaderCells,
  highestLevelWithData,
  levelOfGroup,
} from "./gridColumns";
import { CancelFlightPopover } from "./CancelFlightPopover";
import { DeleteReasonPopover } from "./DeleteReasonPopover";
import { ReasonPopover } from "./ReasonPopover";
import { StatusLegend } from "./StatusLegend";

interface Props {
  sector: Sector;
  flightDate: string;
  flights: Flight[];
  loading: boolean;
  search: string;
  readOnly?: boolean;
  readOnlyReason?: string;
  filter: QuickFilter;
  onFilterChange: (filter: QuickFilter) => void;
  onFlightUpserted: (flight: Flight) => void;
  onFlightDeleted: (flightId: number) => void;
  onFocusFlight: (flightId: number | null) => void;
}

export function FlightGrid({ sector, flightDate, flights, loading, search, readOnly, readOnlyReason, filter, onFilterChange, onFlightUpserted, onFlightDeleted, onFocusFlight }: Props) {
  const { secReasons, revReasons, refreshReasonCodes, presence } = useAppContext();
  const [newVuelo, setNewVuelo] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyFor, setHistoryFor] = useState<Flight | null>(null);
  const [popover, setPopover] = useState<{ category: "SEC" | "REV"; flightId: number; key: string; anchorLabel: string } | null>(null);
  const [deletePrompt, setDeletePrompt] = useState<{ flightId: number; vuelo: string } | null>(null);
  const [cancelPrompt, setCancelPrompt] = useState<Flight | null>(null);
  const [userLevel, setUserLevel] = useState(1);
  const [lookup, setLookup] = useState<ItineraryLookup | null>(null);

  // Validación en vivo del call sign contra el itinerario DGAC mientras se
  // tipea, antes de agregar el vuelo (debounce de 350 ms).
  useEffect(() => {
    const callSign = newVuelo.trim().toUpperCase();
    if (callSign.length < 3) {
      setLookup(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const result = await api.lookupItinerary(flightDate, callSign);
        if (!cancelled) setLookup(result);
      } catch {
        if (!cancelled) setLookup(null);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [newVuelo, flightDate]);

  const dataLevel = useMemo(() => highestLevelWithData(flights), [flights]);
  const maxLevel = Math.max(userLevel, dataLevel);

  const visibleColumns = useMemo(
    () => COLUMNS.filter((c) => {
      const lvl = levelOfGroup(c.group);
      return lvl === null || lvl <= maxLevel;
    }),
    [maxLevel],
  );

  useEffect(() => {
    return () => onFocusFlight(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visibleFlights = useMemo(() => {
    let list = flights;
    if (search.trim()) {
      const q = search.trim().toUpperCase();
      list = list.filter((f) => f.vuelo.toUpperCase().includes(q));
    }
    return applyQuickFilter(list, filter);
  }, [flights, search, filter]);

  const counts = useMemo(() => computeQuickFilterCounts(flights), [flights]);

  async function commit(flightId: number, key: string, value: string) {
    const payload: Record<string, string> = { [key]: value };
    // Al registrar el motivo de una revisión (REV n), la hora de esa llamada
    // (H. REV n) se completa sola con la hora UTC actual si estaba vacía:
    // el operador no tiene que mirar el reloj y tipearla.
    const hRevKey = REV_TO_HREV[key];
    if (hRevKey && value) {
      const flight = flights.find((f) => f.id === flightId);
      if (flight && !(flight as any)[hRevKey]) payload[hRevKey] = horaHhmmUtc();
    }
    const updated = await api.updateFlight(flightId, payload as any);
    onFlightUpserted(updated);
  }

  function openAddNew(category: "SEC" | "REV", flightId: number, key: string, anchorLabel: string) {
    setPopover({ category, flightId, key, anchorLabel });
  }

  async function saveNewReason(code: string, description: string) {
    if (!popover) return;
    await api.addReasonCode(popover.category, code, description);
    await refreshReasonCodes();
    await commit(popover.flightId, popover.key, code);
    setPopover(null);
  }

  async function addFlight() {
    if (!newVuelo.trim()) return;
    setBusy(true);
    try {
      const created = await api.createFlight({ sector, flight_date: flightDate, vuelo: newVuelo.trim().toUpperCase() });
      setNewVuelo("");
      onFlightUpserted(created);
    } finally {
      setBusy(false);
    }
  }

  function removeFlight(id: number) {
    const flight = flights.find((f) => f.id === id);
    setDeletePrompt({ flightId: id, vuelo: flight?.vuelo ?? "este vuelo" });
  }

  async function confirmDelete(reason: string) {
    if (!deletePrompt) return;
    const { flightId } = deletePrompt;
    setDeletePrompt(null);
    try {
      await api.deleteFlight(flightId, reason);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // Ya no existe en el servidor (lo borró otra estación, o ya se había
        // borrado antes) -- se saca igual de la grilla local en vez de dejar
        // una fila "zombie" que nunca se puede eliminar desde la UI.
        onFlightDeleted(flightId);
        return;
      }
      throw e;
    }
    onFlightDeleted(flightId);
  }

  // Cancelación manual: llamaron avisando que el vuelo se canceló. La fila
  // queda en la grilla (tachada, roja) como registro; no se borra. Restaurar
  // (quitar la cancelación) no pide confirmación, solo marcarlo como
  // cancelado -- por eso el popover.
  function toggleCancel(flight: Flight) {
    if (!flight.cancelado) {
      setCancelPrompt(flight);
      return;
    }
    applyCancel(flight, false);
  }

  async function confirmCancel() {
    if (!cancelPrompt) return;
    const flight = cancelPrompt;
    setCancelPrompt(null);
    await applyCancel(flight, true);
  }

  async function applyCancel(flight: Flight, cancelado: boolean) {
    const updated = await api.updateFlight(flight.id, { cancelado } as any);
    onFlightUpserted(updated);
  }

  const groupHeaders = useMemo(() => groupHeaderCells(visibleColumns), [visibleColumns]);

  /* Ancho mínimo = suma de las columnas visibles (más la de N° y la de
     acciones). Sin esto, con table-layout:fixed la tabla se encoge al
     contenedor y al expandir revisiones todas las columnas quedan apretadas
     (SEC/REV bajaban a ~46px y recortaban el código del motivo). Con el
     mínimo, cada columna conserva su ancho declarado y la grilla scrollea
     horizontalmente, que es para lo que existen las columnas fijas. */
  const minTableWidth = useMemo(
    () => ROW_NUMBER_COL_WIDTH + ACTIONS_COL_WIDTH + visibleColumns.reduce((sum, c) => sum + (c.width ?? 60), 0),
    [visibleColumns],
  );

  // Navegación tipo Excel: ↑/↓ saltan de fila en la misma columna (no tienen
  // significado nativo dentro de un input de una sola línea, así que siempre
  // se interceptan); ←/→ solo saltan de celda cuando el cursor ya está en el
  // borde del texto, para no romper la edición normal. Se basa en la
  // posición real en el DOM (cellIndex/hermanos), no en estado de React --
  // así funciona sin importar qué columnas estén visibles en ese momento.
  function focusCell(cell: Element | null | undefined, edge?: "start" | "end") {
    const focusable = cell?.querySelector<HTMLInputElement | HTMLSelectElement>(
      "input.grid-cell-input, select.grid-cell-select"
    );
    if (!focusable) return false;
    focusable.focus();
    if (focusable instanceof HTMLInputElement) {
      const pos = edge === "start" ? 0 : focusable.value.length;
      focusable.setSelectionRange(pos, pos);
    }
    return true;
  }

  function handleGridKeyDown(e: React.KeyboardEvent<HTMLTableElement>) {
    const target = e.target as HTMLElement;
    if (!(target instanceof HTMLInputElement) || !target.classList.contains("grid-cell-input")) return;
    const td = target.closest("td");
    const tr = td?.closest("tr");
    if (!td || !tr) return;

    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      const targetRow = e.key === "ArrowUp" ? tr.previousElementSibling : tr.nextElementSibling;
      const targetCell = targetRow?.children[td.cellIndex];
      if (focusCell(targetCell)) e.preventDefault();
      return;
    }

    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const atStart = target.selectionStart === 0 && target.selectionEnd === 0;
      const atEnd = target.selectionStart === target.value.length && target.selectionEnd === target.value.length;
      if (e.key === "ArrowLeft" && !atStart) return;
      if (e.key === "ArrowRight" && !atEnd) return;
      const dir = e.key === "ArrowLeft" ? "previousElementSibling" : "nextElementSibling";
      let cell = td[dir] as Element | null;
      while (cell) {
        if (focusCell(cell, e.key === "ArrowLeft" ? "end" : "start")) {
          e.preventDefault();
          return;
        }
        cell = cell[dir] as Element | null;
      }
    }
  }

  return (
    <div className="flight-grid-wrap">
      <div className="flight-grid-toolbar">
        {!readOnly && (
          <>
            <div className="new-flight-box">
              <input
                placeholder="Nuevo vuelo (ej. LPE2026)"
                value={newVuelo}
                onChange={(e) => setNewVuelo(e.target.value)}
                onKeyDown={(e) => {
                  // Tab o Enter registran el vuelo directamente, sin tener que ir
                  // al botón. Con Tab el foco se queda acá, listo para el próximo.
                  if ((e.key === "Tab" || e.key === "Enter") && newVuelo.trim()) {
                    e.preventDefault();
                    addFlight();
                  }
                }}
              />
              {lookup && lookup.found && (
                <span className={`itin-hint ${lookup.fuente === "dia_anterior" ? "prev" : "ok"}`}>
                  {lookup.fuente === "dia_anterior" ? "✓ Itin. día anterior" : "✓ En itinerario DGAC"}
                  {lookup.hora_utc && ` · SLOT ${lookup.hora_utc}`}
                  {lookup.aerodromo && ` · ${lookup.aerodromo}`}
                </span>
              )}
              {lookup && !lookup.found && (
                <span className="itin-hint none" title="Puede ser militar/policial — verificar permiso antes de autorizar">
                  Sin coincidencia en itinerario DGAC
                </span>
              )}
            </div>
            <button onClick={addFlight} disabled={busy}>+ Agregar vuelo</button>
          </>
        )}
        {readOnly && (
          <span className="readonly-badge" title={readOnlyReason ?? "Solo lectura — no gestionas esta zona"}>
            Solo lectura
          </span>
        )}

        <div className="level-control" role="group" aria-label="Revisiones visibles">
          <span className="level-control-label">Revisiones</span>
          {[1, 2, 3, 4, 5].map((lvl) => {
            const locked = lvl <= dataLevel; // hay datos: no se puede ocultar
            return (
              <button
                key={lvl}
                className={`level-btn${lvl <= maxLevel ? " on" : ""}`}
                aria-pressed={lvl <= maxLevel}
                title={
                  locked && lvl > 1
                    ? `La revisión ${lvl} tiene datos cargados y no puede ocultarse`
                    : lvl <= maxLevel
                      ? `Mostrar solo hasta la revisión ${Math.max(lvl - 1, 1)}`
                      : `Expandir hasta la revisión ${lvl}`
                }
                onClick={() => setUserLevel(lvl <= maxLevel ? Math.max(lvl - 1, 1) : lvl)}
              >
                {lvl}
              </button>
            );
          })}
        </div>

        <span className="flight-grid-utc-note">Horas en UTC</span>
      </div>

      <div className="quick-filters" role="tablist" aria-label="Filtros rápidos">
        <button
          className={filter === "pending" ? "active" : ""}
          onClick={() => onFilterChange(filter === "pending" ? "all" : "pending")}
          title="Vuelos que todavía no tienen hora de despegue (DEP). Los demás filtros (Todos, Con alerta, Cancelados, Sin itinerario) están en el menú ☰"
        >
          Pendientes <span className="count">{counts.pending}</span>
        </button>
      </div>

      <StatusLegend />

      <div className="flight-grid-scroll">
        <table className="flight-grid" style={{ minWidth: minTableWidth }} onKeyDown={handleGridKeyDown}>
          {/* Anchos deterministas: sin esto la tabla usa layout automático y
              cada columna se estira hasta su contenido más ancho. El caso que
              rompía la grilla al expandir revisiones eran las celdas SEC/REV:
              un <select> se dimensiona según su opción más larga, y la opción
              "+ Agregar nuevo motivo…" es mucho más ancha que los códigos, así
              que esas columnas se comían el espacio de las demás. Con
              table-layout:fixed manda el <colgroup> y el contenido se adapta. */}
          <colgroup>
            <col style={{ width: ROW_NUMBER_COL_WIDTH }} />
            {visibleColumns.map((c) => (
              <col key={c.key} style={{ width: c.width }} />
            ))}
            <col style={{ width: ACTIONS_COL_WIDTH }} />
          </colgroup>
          <thead>
            <tr className="group-header-row">
              <th rowSpan={2} className="sticky-col sticky-col-1"></th>
              {groupHeaders.map((g, i) => (
                <th key={i} colSpan={g.span} className={`group-header col-group-${g.group}`}>
                  {GROUP_LABELS[g.group]}
                </th>
              ))}
              <th rowSpan={2} className="actions-col"></th>
            </tr>
            <tr>
              {visibleColumns.map((c) => {
                const stickyClass = c.key === "hora" ? " sticky-col sticky-col-2" : c.key === "vuelo" ? " sticky-col sticky-col-3" : "";
                const shadeClass = c.shade ? ` shade-${c.shade}` : "";
                return (
                  <th key={c.key} className={`col-group-${c.group}${stickyClass}${shadeClass}${c.hint ? " has-hint" : ""}`} title={c.hint}>
                    {c.label}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <>
                {[0, 1, 2].map((i) => (
                  <tr key={`skeleton-${i}`} className="skeleton-row">
                    <td colSpan={visibleColumns.length + 2}><div className="skeleton-bar" /></td>
                  </tr>
                ))}
              </>
            )}
            {!loading && visibleFlights.map((f) => (
              <FlightRow
                key={f.id}
                flight={f}
                columns={visibleColumns}
                secOptions={secReasons}
                revOptions={revReasons}
                presenceName={presence[f.id]?.operator_name}
                readOnly={readOnly}
                onCommit={commit}
                onAddNew={openAddNew}
                onDelete={removeFlight}
                onToggleCancel={toggleCancel}
                onShowHistory={setHistoryFor}
                onFocusRow={onFocusFlight}
              />
            ))}
            {!loading && visibleFlights.length === 0 && (
              <tr>
                <td colSpan={visibleColumns.length + 2} className="grid-empty">
                  {flights.length === 0 ? "Sin vuelos para esta fecha todavía." : "Ningún vuelo coincide con el filtro/búsqueda actual."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {historyFor && (
        <HistoryModal flightId={historyFor.id} vuelo={historyFor.vuelo} onClose={() => setHistoryFor(null)} />
      )}

      {popover && (
        <ReasonPopover
          category={popover.category}
          anchorLabel={popover.anchorLabel}
          onSave={saveNewReason}
          onClose={() => setPopover(null)}
        />
      )}

      {deletePrompt && (
        <DeleteReasonPopover
          vuelo={deletePrompt.vuelo}
          onConfirm={confirmDelete}
          onClose={() => setDeletePrompt(null)}
        />
      )}

      {cancelPrompt && (
        <CancelFlightPopover
          vuelo={cancelPrompt.vuelo}
          onConfirm={confirmCancel}
          onClose={() => setCancelPrompt(null)}
        />
      )}
    </div>
  );
}
