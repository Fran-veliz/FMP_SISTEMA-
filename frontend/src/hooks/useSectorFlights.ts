import { useCallback, useRef, useState } from "react";
import { api } from "../services/api";
import type { Flight, Sector } from "../types";

export function useSectorFlights(sector: Sector | null, flightDate: string) {
  const [flights, setFlights] = useState<Flight[]>([]);
  const [loading, setLoading] = useState(true);
  const loadRequestId = useRef(0);

  const loadFlights = useCallback(async () => {
    if (!sector) return;
    const requestId = ++loadRequestId.current;
    setLoading(true);
    const data = await api.listFlights(sector, flightDate);
    if (requestId === loadRequestId.current) {
      setFlights(data);
      setLoading(false);
    }
  }, [sector, flightDate]);

  // Tanto la respuesta de una edición como el WS actualizan solo la fila
  // afectada. Cada instancia acepta únicamente su sector y fecha.
  const upsertFlight = useCallback((flight: Flight) => {
    if (!sector || flight.sector !== sector || flight.flight_date !== flightDate) return;
    setFlights((prev) => {
      const idx = prev.findIndex((f) => f.id === flight.id);
      if (idx === -1) return [...prev, flight].sort((a, b) => a.numero_fila - b.numero_fila);
      const next = [...prev];
      next[idx] = flight;
      return next;
    });
  }, [sector, flightDate]);

  const removeFlight = useCallback((flightId: number) => {
    setFlights((prev) => prev.filter((f) => f.id !== flightId));
  }, []);

  return { flights, loading, loadFlights, upsertFlight, removeFlight };
}
