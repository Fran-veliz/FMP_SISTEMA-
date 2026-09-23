import { useState } from "react";

interface Props {
  vuelo: string;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}

/** Reemplaza el confirm() nativo al eliminar una fila de la grilla: pide el
 * motivo del borrado (queda guardado en flight_deletion_log.deletion_reason,
 * junto al resto de la auditoría -- quién y cuándo). Mismo lenguaje visual
 * que ReasonPopover/CoveragePopover. */
export function DeleteReasonPopover({ vuelo, onConfirm, onClose }: Props) {
  const [reason, setReason] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!reason.trim()) return;
    onConfirm(reason.trim());
  }

  return (
    <div className="reason-popover-backdrop" onClick={onClose}>
      <form className="reason-popover delete-reason-popover" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <p className="reason-popover-title">Eliminar {vuelo} de la grilla</p>
        <label>
          Motivo del borrado
          <input
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Ej. Vuelo cargado por error"
            maxLength={300}
          />
        </label>
        <p className="delete-reason-hint">Se guarda junto con quién y cuándo lo eliminó, para auditoría.</p>
        <div className="reason-popover-actions">
          <button type="button" className="ghost" onClick={onClose}>Cancelar</button>
          <button type="submit" className="delete-reason-confirm" disabled={!reason.trim()}>Eliminar</button>
        </div>
      </form>
    </div>
  );
}
