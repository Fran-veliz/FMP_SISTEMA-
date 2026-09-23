import { CloseIcon } from "../shared/Icons";

interface Props {
  vuelo: string;
  onConfirm: () => void;
  onClose: () => void;
}

/** Confirmación antes de marcar un vuelo como CANCELADO -- mismo lenguaje
 * visual que CoveragePopover/DeleteReasonPopover, en vez de un confirm()
 * nativo. */
export function CancelFlightPopover({ vuelo, onConfirm, onClose }: Props) {
  return (
    <div className="reason-popover-backdrop" onClick={onClose}>
      <div className="coverage-popover" onClick={(e) => e.stopPropagation()}>
        <div className="coverage-popover-icon cancel-flight-icon"><CloseIcon /></div>
        <p className="reason-popover-title">Marcar {vuelo} como CANCELADO</p>
        <p className="coverage-popover-note">
          La fila queda en la grilla, tachada, como registro de que llamaron avisando la
          cancelación — no se borra ni se pierde su historial.
        </p>
        <div className="reason-popover-actions">
          <button type="button" className="ghost" onClick={onClose}>Volver</button>
          <button type="button" className="cancel-flight-confirm" onClick={onConfirm}>Marcar cancelado</button>
        </div>
      </div>
    </div>
  );
}
