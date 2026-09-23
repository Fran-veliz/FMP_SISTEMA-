import { useCallback, useEffect, useState } from "react";
import { api } from "../../services/api";
import { diaMesUtc, fechaIsoUtc, horaUtc, hoyIso, sumarDiasIso } from "../../utils/utcDate";
import type { Shift } from "../../types";

interface Props {
  currentShiftId: number;
  onClockOut: () => void;
}

function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  return `${h}h ${m}min`;
}

/** Cómo se relaciona un turno con la jornada que se está mirando.
 *
 * Un turno no cabe siempre dentro de un día: los relevos son cada ~2 h y el
 * que entra a las 22:00 cierra pasada la medianoche. Ese turno cubre las
 * primeras horas del día siguiente, así que aparece en los dos -- y en cada
 * uno hay que decir de dónde viene o hacia dónde sigue, o la hora sola
 * engaña: un "Inicio 22:00" en la jornada del 23 parece un turno que empezó
 * esa noche, cuando en realidad venía del 22. */
function tramo(s: Shift, fecha: string) {
  const inicio = fechaIsoUtc(s.start_time);
  const fin = fechaIsoUtc(s.end_time);
  return {
    vieneDeAntes: inicio !== null && inicio < fecha,
    sigueDespues: fin !== null && fin > fecha,
    abierto: s.end_time === null,
  };
}

export function BitacoraPanel({ currentShiftId, onClockOut }: Props) {
  const [active, setActive] = useState<Shift[]>([]);
  const [history, setHistory] = useState<Shift[]>([]);
  const [, forceTick] = useState(0);
  const [closing, setClosing] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hoy = hoyIso();
  const [fecha, setFecha] = useState(hoy);

  const refresh = useCallback(async () => {
    try {
      const [a, h] = await Promise.all([api.activeShifts(), api.listShifts(fecha)]);
      setActive(a);
      setHistory(h);
      setError(null);
    } catch (e) {
      // Un perfil acotado al año en curso recibe 403 al pedir una jornada de
      // otro año. Se muestra el motivo en vez de dejar la tabla vacía, que
      // parecería que ese día no trabajó nadie.
      setError((e as Error).message ?? "No se pudo cargar la bitácora");
      setHistory([]);
    }
  }, [fecha]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10_000);
    return () => clearInterval(interval);
  }, [refresh]);

  // Duración en vivo (Hallazgo F11): recalcula cada 30s sin esperar al refresh del servidor.
  useEffect(() => {
    const tick = setInterval(() => forceTick((n) => n + 1), 30_000);
    return () => clearInterval(tick);
  }, []);

  async function handleClockOut() {
    setBusy(true);
    try {
      await api.clockOut(currentShiftId, note.trim() || undefined);
      onClockOut();
      refresh();
    } finally {
      setBusy(false);
      setClosing(false);
    }
  }

  return (
    <div className="bitacora-panel">
      <section>
        <h2>En turno ahora</h2>
        {active.length === 0 && <p className="bitacora-empty">Nadie en turno</p>}
        <div className="shift-cards">
          {active.map((s) => (
            <div key={s.id} className={`shift-card${s.id === currentShiftId ? " mine" : ""}${s.excede_maximo ? " excedido" : ""}`}>
              <span className="live-dot" aria-hidden="true" />
              <div className="shift-card-body">
                <span className="shift-card-name">
                  {s.operator_name}
                  {s.excede_maximo && (
                    <span
                      className="shift-card-warn"
                      title={`Turno abierto hace más de ${s.maximo_horas} h: puede que se haya olvidado de cerrarlo`}
                    >
                      excede {s.maximo_horas} h
                    </span>
                  )}
                </span>
                {/* La duración sale del servidor (horas_en_turno) y no de
                    minutesSince: si se calculara acá, el texto y la insignia
                    de excedido podrían contradecirse cuando el reloj de esta
                    computadora está corrido. */}
                <span className="shift-card-meta">{s.position} · desde {horaUtc(s.start_time)} UTC · hace {formatDuration(s.horas_en_turno * 60)}</span>
              </div>
              {s.id === currentShiftId && (
                <button onClick={() => setClosing(true)}>Fin de turno</button>
              )}
            </div>
          ))}
        </div>
      </section>

      <section>
        <div className="bitacora-header">
          <h2>Turnos de la jornada</h2>
          <div className="bitacora-toolbar">
            <button
              className="ghost"
              onClick={() => setFecha(sumarDiasIso(fecha, -1))}
              title="Día anterior"
              aria-label="Día anterior"
            >
              ‹
            </button>
            <input
              type="date"
              value={fecha}
              max={hoy}
              onChange={(e) => setFecha(e.target.value || hoy)}
              aria-label="Jornada que se consulta"
            />
            <button
              className="ghost"
              onClick={() => setFecha(sumarDiasIso(fecha, 1))}
              disabled={fecha >= hoy}
              title="Día siguiente"
              aria-label="Día siguiente"
            >
              ›
            </button>
            {fecha !== hoy && (
              <button className="ghost" onClick={() => setFecha(hoy)}>Hoy</button>
            )}
          </div>
        </div>

        <p className="bitacora-hint">
          Quién estuvo de turno ese día y en qué horario, en UTC. Se listan también
          los turnos que vienen del día anterior o siguen en el siguiente: cubren
          parte de esta jornada aunque no hayan empezado en ella.
        </p>

        {error && <p className="bitacora-error">{error}</p>}

        <table className="bitacora-table">
          <thead>
            <tr><th>Operador FMP</th><th>Posición</th><th>Inicio (UTC)</th><th>Fin (UTC)</th><th>Duración</th><th>Nota de relevo</th></tr>
          </thead>
          <tbody>
            {history.map((s) => {
              const t = tramo(s, fecha);
              return (
                <tr key={s.id} className={t.vieneDeAntes || t.sigueDespues ? "cruza-jornada" : undefined}>
                  <td>{s.operator_name}</td>
                  <td>{s.position}</td>
                  <td>
                    {horaUtc(s.start_time)}
                    {t.vieneDeAntes && (
                      <span
                        className="bitacora-tramo"
                        title={`El turno empezó el ${diaMesUtc(s.start_time)} y siguió hasta esta jornada`}
                      >
                        viene del {diaMesUtc(s.start_time)}
                      </span>
                    )}
                  </td>
                  <td>
                    {t.abierto ? <span className="bitacora-tramo en-curso">en curso</span> : horaUtc(s.end_time)}
                    {t.sigueDespues && (
                      <span
                        className="bitacora-tramo"
                        title={`El turno pasó de la medianoche y cerró el ${diaMesUtc(s.end_time!)}`}
                      >
                        cierra el {diaMesUtc(s.end_time!)}
                      </span>
                    )}
                  </td>
                  <td>{s.duration_minutes != null ? formatDuration(s.duration_minutes) : "—"}</td>
                  <td className="bitacora-note">{s.handover_note ?? "—"}</td>
                </tr>
              );
            })}
            {history.length === 0 && !error && (
              <tr><td colSpan={6} className="bitacora-empty">Sin turnos registrados esa jornada</td></tr>
            )}
          </tbody>
        </table>
      </section>

      {closing && (
        <div className="reason-popover-backdrop" onClick={() => !busy && setClosing(false)}>
          <div className="reason-popover handover-popover" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h4 className="reason-popover-title">Fin de turno</h4>
            <label>
              Nota de relevo (opcional) — qué queda pendiente para quien te releva
              <textarea
                className="handover-textarea"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Ej. LPE2026 espera confirmación de CTOT 2; SKU801 aún sin ETD…"
                rows={4}
                maxLength={1000}
                autoFocus
              />
            </label>
            <div className="reason-popover-actions">
              <button className="ghost" onClick={() => setClosing(false)} disabled={busy}>Cancelar</button>
              <button onClick={handleClockOut} disabled={busy}>
                {busy ? "Cerrando…" : "Cerrar turno"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
