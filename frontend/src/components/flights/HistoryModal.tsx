import { useEffect, useState } from "react";
import { api } from "../../services/api";
import type { FlightHistoryEntry } from "../../types";
import { FIELD_LABELS } from "../../types";
import { fechaHoraCompletaUtc } from "../../utils/utcDate";
import { CloseIcon } from "../shared/Icons";

interface Props {
  flightId: number;
  vuelo: string;
  onClose: () => void;
}

/** Quién cambió qué y cuándo, para un vuelo. Se abre desde la grilla. */
export function HistoryModal({ flightId, vuelo, onClose }: Props) {
  const [history, setHistory] = useState<FlightHistoryEntry[] | null>(null);

  useEffect(() => {
    api.flightHistory(flightId).then(setHistory);
  }, [flightId]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="history-modal-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 id="history-modal-title">Historial — {vuelo}</h3>
          <button className="modal-close" onClick={onClose} autoFocus><CloseIcon /></button>
        </div>
        {history === null && <p>Cargando…</p>}
        {history !== null && history.length === 0 && <p className="bitacora-empty">Sin cambios registrados todavía.</p>}
        {history !== null && history.length > 0 && (
          <table className="history-table">
            <thead>
              <tr><th>Campo</th><th>Antes</th><th>Después</th><th>Quién</th><th>Cuándo</th></tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td>{FIELD_LABELS[h.field_name] ?? h.field_name}</td>
                  <td>{h.old_value ?? "—"}</td>
                  <td>{h.new_value ?? "—"}</td>
                  <td>{h.operator_name ?? "—"}</td>
                  <td>{fechaHoraCompletaUtc(h.changed_at)} UTC</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
