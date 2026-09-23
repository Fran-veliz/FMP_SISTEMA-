import type { Sector } from "../../types";
import { ShieldIcon } from "../shared/Icons";

interface Props {
  sector: Sector;
  operatorName: string;
  activeOperators: string[];
  onConfirm: () => void;
  onClose: () => void;
}

/** Confirmación antes de activar el modo cobertura -- mismo lenguaje visual
 * que ReasonPopover (backdrop + card), en vez de un confirm() nativo. Muestra
 * quién tiene la posición ajena abierta ahora mismo, para que quien activa
 * la cobertura sepa a quién está reemplazando. */
export function CoveragePopover({ sector, operatorName, activeOperators, onConfirm, onClose }: Props) {
  return (
    <div className="reason-popover-backdrop" onClick={onClose}>
      <div className="coverage-popover" onClick={(e) => e.stopPropagation()}>
        <div className="coverage-popover-icon"><ShieldIcon /></div>
        <p className="reason-popover-title">Activar modo cobertura — FMP {sector}</p>
        <p className="coverage-popover-status">
          {activeOperators.length > 0
            ? `Turno abierto actualmente: ${activeOperators.join(", ")}`
            : "No hay ningún turno abierto en esta posición ahora mismo."}
        </p>
        <p className="coverage-popover-note">
          Vas a poder editar FMP {sector} además de tu posición. Usalo solo si quedaste como único
          operador FMP disponible para las dos zonas. Cada cambio que hagas ahí va a quedar registrado
          con tu nombre ({operatorName}) en el historial del vuelo, igual que cualquier otra edición.
        </p>
        <div className="reason-popover-actions">
          <button type="button" className="ghost" onClick={onClose}>Cancelar</button>
          <button type="button" className="coverage-popover-confirm" onClick={onConfirm}>Activar cobertura</button>
        </div>
      </div>
    </div>
  );
}
