import { useState } from "react";
import { CloseIcon } from "../shared/Icons";

const LEGEND_DISMISS_KEY = "ctot_legend_dismissed";

/** Los estados de color no tenían ninguna explicación visible sin pasar el
 * mouse (Hallazgo F7). Visible por defecto; se puede ocultar, y queda un
 * enlace chico para volver a mostrarla. */
export function StatusLegend() {
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(LEGEND_DISMISS_KEY) === "1");

  function dismiss() {
    localStorage.setItem(LEGEND_DISMISS_KEY, "1");
    setDismissed(true);
  }

  if (dismissed) {
    return (
      <button className="legend-reopen" onClick={() => setDismissed(false)}>
        ¿Qué significan los colores?
      </button>
    );
  }

  return (
    <div className="status-legend">
      <span className="legend-item"><span className="legend-dot status-stripe-nuevo" /> Nuevo</span>
      <span className="legend-item"><span className="legend-dot status-stripe-en-gestion" /> En gestión</span>
      <span className="legend-item"><span className="legend-dot status-stripe-confirmado" /> Confirmado</span>
      <span className="legend-item"><span className="legend-dot status-stripe-alerta" /> Falta motivo / hora inválida</span>
      <span className="legend-item"><span className="legend-dot status-stripe-cancelado" /> Cancelado</span>
      <button className="legend-close" onClick={dismiss} title="Ocultar leyenda"><CloseIcon /></button>
    </div>
  );
}
