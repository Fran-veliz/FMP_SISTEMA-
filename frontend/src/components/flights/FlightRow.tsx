import { memo, useEffect, useRef, useState } from "react";
import type { Flight, ReasonCode } from "../../types";
import { computeFlightStatus, isValidHHMM, needsReason } from "../../utils/flightStatus";
import { ClockIcon, TrashIcon } from "../shared/Icons";

export interface ColumnDef {
  key: keyof Flight;
  label: string;
  width?: number;
  time?: boolean;
  group: string;
  // Dentro de un mismo grupo de nivel, distingue la columna "disparadora"
  // (H.REV/REV, tono fuerte) de las de asignación (ETD/CTOT/SEC, tono
  // pastel) -- ver bloques de color en App.css (.shade-strong/.shade-pastel).
  shade?: "strong" | "pastel";
  hint?: string; // tooltip del encabezado: qué significa el campo
}

/* El ancho lo fija la columna (ver <colgroup> en FlightGrid); el input solo
   ocupa el 100 % de su celda. */
function EditableText({
  value,
  onCommit,
  timeField,
}: {
  value: string | null;
  onCommit: (v: string) => void;
  timeField?: boolean;
  onFocus?: () => void;
}) {
  const [draft, setDraft] = useState(value ?? "");
  const ref = useRef<HTMLInputElement>(null);

  // Si el valor cambia desde afuera (otra estación por WS, o un campo
  // autocompletado por el servidor, ej. H. REV al elegir el motivo), se
  // refleja en la celda — salvo que el operador la esté editando.
  useEffect(() => {
    if (document.activeElement !== ref.current) setDraft(value ?? "");
  }, [value]);

  const invalid = timeField && !isValidHHMM(draft);
  return (
    <input
      ref={ref}
      className={`grid-cell-input${invalid ? " grid-cell-invalid" : ""}`}
      value={draft}
      // Si el valor no entra en la celda (tipico en OBSERVACIONES, que es
      // texto libre), al menos se lee completo al pasar el mouse.
      title={
        invalid
          ? "Formato de hora esperado: HHMM (ej. 2215) — se guarda igual, solo es un aviso"
          : draft || undefined
      }
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft !== (value ?? "")) onCommit(draft);
      }}
    />
  );
}

const ADD_NEW_OPTION = "__add_new__";

function EditableSelect({
  value,
  options,
  onCommit,
  onAddNew,
  warn,
}: {
  value: string | null;
  options: ReasonCode[];
  onCommit: (v: string) => void;
  onAddNew: () => void;
  warn?: boolean;
}) {
  return (
    <select
      className={`grid-cell-select${warn ? " grid-cell-warn" : ""}`}
      value={value ?? ""}
      title={warn ? "Falta indicar el motivo de esta revisión" : undefined}
      onChange={(e) => {
        if (e.target.value === ADD_NEW_OPTION) onAddNew();
        else onCommit(e.target.value);
      }}
    >
      <option value="" />
      {options.map((o) => (
        <option key={o.code} value={o.code} title={o.description ?? undefined}>{o.code}</option>
      ))}
      <option value={ADD_NEW_OPTION}>+ Agregar nuevo motivo…</option>
    </select>
  );
}

const SEC_COLS = new Set(["sec1", "sec2", "sec3", "sec4", "sec5"]);
const REV_COLS = new Set(["rev1", "rev2", "rev3", "rev4"]);
const READONLY_COLS = new Set(["eta_aircon_calculado", "slot_arr_dgac", "dla_minutos"]);

interface Props {
  flight: Flight;
  columns: ColumnDef[];
  secOptions: ReasonCode[];
  revOptions: ReasonCode[];
  presenceName?: string;
  readOnly?: boolean;
  onCommit: (flightId: number, key: string, value: string) => void;
  onAddNew: (category: "SEC" | "REV", flightId: number, key: string, anchorLabel: string) => void;
  onDelete: (flightId: number) => void;
  onToggleCancel: (flight: Flight) => void;
  onShowHistory: (flight: Flight) => void;
  onFocusRow: (flightId: number) => void;
}

function FlightRowImpl({
  flight, columns, secOptions, revOptions, presenceName, readOnly,
  onCommit, onAddNew, onDelete, onToggleCancel, onShowHistory, onFocusRow,
}: Props) {
  const status = computeFlightStatus(flight);

  return (
    <tr
      className={`status-${status}`}
      onFocus={() => onFocusRow(flight.id)}
    >
      <td className="grid-cell-numero sticky-col sticky-col-1">
        <span className={`status-stripe status-stripe-${status}`} aria-hidden="true" />
        {flight.numero_fila}
      </td>
      {columns.map((c) => {
        // Todas las columnas editables/mostradas acá son de tipo string|null
        // en Flight (las numéricas/booleanas -- dla_minutos, cancelado --
        // no pasan por EditableText/EditableSelect). ColumnDef.key sigue
        // tipado como keyof Flight para que un typo en COLUMNS no compile.
        const value = flight[c.key] as string | null;
        const stickyClass = c.key === "hora" ? " sticky-col sticky-col-2" : c.key === "vuelo" ? " sticky-col sticky-col-3" : "";
        const shadeClass = c.shade ? ` shade-${c.shade}` : "";

        // HORA (hora de la comunicación) es inmutable: se registra sola al
        // crear el vuelo y nunca se edita.
        if (c.key === "hora") {
          return (
            <td key={c.key} className={`grid-cell-readonly col-group-${c.group}${stickyClass}${shadeClass}`}>
              {value ?? ""}
            </td>
          );
        }
        if (c.key === "vuelo") {
          const sinItinerario = !flight.cancelado && !flight.slot_arr_dgac;
          return (
            <td key={c.key} className={`col-group-${c.group}${stickyClass}${shadeClass}`}>
              {/* Orden fijo: call sign, reloj, insignias. El reloj va segundo
                  -- y no al final -- para que caiga siempre en la misma
                  columna: es un ancla visual, y detrás de él las insignias
                  arrancan todas alineadas entre filas. */}
              <div className="vuelo-cell">
                <span className="vuelo-callsign">{value}</span>
                <button className="history-btn" title="Ver historial de cambios" onClick={() => onShowHistory(flight)}>
                  <ClockIcon />
                </button>
                {sinItinerario && (
                  <span
                    className="badge-sin-itin"
                    title="Este vuelo no aparece en el itinerario DGAC de esta fecha — verificar permiso antes de autorizar el despegue"
                  >
                    SIN ITIN.
                  </span>
                )}
                {flight.cancelado && <span className="badge-cancelado">CANCELADO</span>}
                {presenceName && (
                  <span className="presence-chip" title={`${presenceName} está viendo este vuelo`}>
                    {presenceName.slice(0, 2).toUpperCase()}
                  </span>
                )}
              </div>
            </td>
          );
        }
        if (READONLY_COLS.has(c.key) || readOnly) {
          return <td key={c.key} className={`grid-cell-readonly col-group-${c.group}${stickyClass}${shadeClass}`}>{value ?? ""}</td>;
        }
        if (SEC_COLS.has(c.key)) {
          return (
            <td key={c.key} className={`col-group-${c.group}${shadeClass}`}>
              <EditableSelect
                value={value}
                options={secOptions}
                onCommit={(v) => onCommit(flight.id, c.key, v)}
                onAddNew={() => onAddNew("SEC", flight.id, c.key, `${flight.vuelo} · ${c.label}`)}
                warn={needsReason(flight, c.key)}
              />
            </td>
          );
        }
        if (REV_COLS.has(c.key)) {
          return (
            <td key={c.key} className={`col-group-${c.group}${shadeClass}`}>
              <EditableSelect
                value={value}
                options={revOptions}
                onCommit={(v) => onCommit(flight.id, c.key, v)}
                onAddNew={() => onAddNew("REV", flight.id, c.key, `${flight.vuelo} · ${c.label}`)}
                warn={needsReason(flight, c.key)}
              />
            </td>
          );
        }
        return (
          <td key={c.key} className={`col-group-${c.group}${stickyClass}${shadeClass}`}>
            <EditableText value={value} timeField={c.time} onCommit={(v) => onCommit(flight.id, c.key, v)} />
          </td>
        );
      })}
      <td className="row-actions">
        {!readOnly && (
          <>
            <button
              className={`grid-cancel-btn${flight.cancelado ? " restore" : ""}`}
              onClick={() => onToggleCancel(flight)}
              title={flight.cancelado ? "Restaurar vuelo (quitar cancelación)" : "Marcar vuelo como CANCELADO (llamaron avisando que se canceló)"}
            >
              {flight.cancelado ? "↩" : "CNL"}
            </button>
            <button className="grid-delete-btn" onClick={() => onDelete(flight.id)} title="Eliminar fila (solo errores de registro)">
              <TrashIcon />
            </button>
          </>
        )}
      </td>
    </tr>
  );
}

export const FlightRow = memo(FlightRowImpl, (prev, next) => {
  return (
    prev.flight === next.flight &&
    prev.columns === next.columns &&
    prev.secOptions === next.secOptions &&
    prev.revOptions === next.revOptions &&
    prev.presenceName === next.presenceName &&
    prev.readOnly === next.readOnly
  );
});
