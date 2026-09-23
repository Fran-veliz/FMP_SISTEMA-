import { useEffect, useRef, useState } from "react";
import { api } from "../../services/api";

// Cada tanto se re-sincroniza contra /server-time para corregir el drift
// del reloj local (browser/laptop) sin depender de él como única fuente.
const RESYNC_MS = 5 * 60 * 1000;

function useUtcNow() {
  const [now, setNow] = useState(() => new Date());
  const offsetMs = useRef(0); // server_time_ms - Date.now() en el momento del sync

  useEffect(() => {
    let cancelled = false;

    async function sync() {
      try {
        const { utc } = await api.serverTime();
        const serverMs = new Date(utc).getTime();
        if (!cancelled && !Number.isNaN(serverMs)) {
          offsetMs.current = serverMs - Date.now();
        }
      } catch {
        // Sin conexión al backend: se sigue mostrando el reloj local tal
        // cual (offset 0) en vez de romper el reloj del header.
      }
    }

    sync();
    const resync = setInterval(sync, RESYNC_MS);

    // Alinea el tick al inicio de cada segundo para que el reloj no "salte".
    let interval: ReturnType<typeof setInterval> | undefined;
    const align = setTimeout(() => {
      setNow(new Date(Date.now() + offsetMs.current));
      interval = setInterval(() => setNow(new Date(Date.now() + offsetMs.current)), 1000);
    }, 1000 - (Date.now() % 1000));

    return () => {
      cancelled = true;
      clearInterval(resync);
      clearTimeout(align);
      if (interval) clearInterval(interval);
    };
  }, []);
  return now;
}

/** Reloj UTC en tiempo real. Toda la operación FMP se maneja en UTC, así que
 * este reloj es la referencia horaria única del sistema (header y login). */
export function UtcClock({ size = "md" }: { size?: "md" | "lg" }) {
  const now = useUtcNow();
  const hh = String(now.getUTCHours()).padStart(2, "0");
  const mm = String(now.getUTCMinutes()).padStart(2, "0");
  const ss = String(now.getUTCSeconds()).padStart(2, "0");
  const dateLabel = now.toLocaleDateString("es-PE", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });

  return (
    <div className={`utc-clock utc-clock-${size}`} role="timer" aria-label={`Hora UTC actual ${hh}:${mm}:${ss}`}>
      <span className="utc-clock-time">
        {hh}
        <span className="utc-colon" aria-hidden="true">:</span>
        {mm}
        <span className="utc-colon" aria-hidden="true">:</span>
        <span className="utc-clock-seconds">{ss}</span>
      </span>
      <span className="utc-clock-label">
        <span className="utc-badge">UTC</span>
        {dateLabel}
      </span>
    </div>
  );
}
