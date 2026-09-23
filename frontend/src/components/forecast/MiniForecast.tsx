import { useEffect, useState } from "react";
import { api } from "../../services/api";
import type { ForecastBucket } from "../../types";

interface Props {
  flightDate: string;
  onOpenFull: () => void;
}

/** Sparkline persistente (hora actual + próximas 3): la vista de mayor valor
 * situacional del sistema no debería exigir salir de la grilla para
 * consultarla (Hallazgo F12). El detalle completo sigue en la pestaña Gráfico. */
export function MiniForecast({ flightDate, onOpenFull }: Props) {
  const [buckets, setBuckets] = useState<ForecastBucket[]>([]);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      const data = await api.forecast(flightDate);
      if (!cancelled) setBuckets(data.buckets);
    }
    refresh();
    const interval = setInterval(refresh, 20_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [flightDate]);

  if (buckets.length === 0) return null;

  const nowHour = new Date().getUTCHours();
  const visible = [0, 1, 2, 3].map((offset) => buckets[(nowHour + offset) % 24]);
  const max = Math.max(...visible.map((b) => Math.max(b.pronosticado, b.capacidad_maxima)), 1);

  return (
    <button className="mini-forecast" onClick={onOpenFull} title="Ver gráfico completo de pronóstico">
      <span className="mini-forecast-label">Próximas horas</span>
      <span className="mini-forecast-bars">
        {visible.map((b, i) => {
          const over = b.pronosticado > b.capacidad_maxima;
          const heightPct = Math.max(6, Math.round((b.pronosticado / max) * 100));
          return (
            <span className="mini-forecast-bar-wrap" key={i}>
              <span
                className={`mini-forecast-bar${over ? " over" : ""}`}
                style={{ height: `${heightPct}%` }}
              />
              <span className="mini-forecast-hour">{i === 0 ? "ahora" : `+${i}h`}</span>
            </span>
          );
        })}
      </span>
    </button>
  );
}
