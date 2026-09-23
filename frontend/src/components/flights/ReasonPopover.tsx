import { useState } from "react";
import type { ReasonCategory } from "../../types";

interface Props {
  category: ReasonCategory;
  anchorLabel: string;
  onSave: (code: string, description: string) => void;
  onClose: () => void;
}

/** Reemplaza el window.prompt() nativo (Hallazgo F8): mismo lenguaje visual
 * que el resto de la app, con validación en línea. */
export function ReasonPopover({ category, anchorLabel, onSave, onClose }: Props) {
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const placeholder = category === "SEC" ? "SxNUEVO" : "RxNUEVO";

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) return;
    onSave(code.trim(), description.trim());
  }

  return (
    <div className="reason-popover-backdrop" onClick={onClose}>
      <form className="reason-popover" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <p className="reason-popover-title">Nuevo motivo {category} — {anchorLabel}</p>
        <label>
          Código
          <input
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={placeholder}
          />
        </label>
        <label>
          Descripción (opcional)
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Motivo en palabras"
          />
        </label>
        <div className="reason-popover-actions">
          <button type="button" className="ghost" onClick={onClose}>Cancelar</button>
          <button type="submit" disabled={!code.trim()}>Guardar</button>
        </div>
      </form>
    </div>
  );
}
