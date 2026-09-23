import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import "./styles/App.css";
import { AppProvider, useAppContext } from "./contexts/AppContext";
import { api } from "./services/api";
import { AppHeader } from "./components/shared/AppHeader";
import { BitacoraPanel } from "./components/shifts/BitacoraPanel";
import { CoveragePopover } from "./components/flights/CoveragePopover";
import { FlightGrid } from "./components/flights/FlightGrid";
import { Login } from "./components/auth/Login";
import { MiniForecast } from "./components/forecast/MiniForecast";
import { CloseIcon, ShieldIcon } from "./components/shared/Icons";
import { NavMenu } from "./components/shared/NavMenu";
import { computeQuickFilterCounts } from "./utils/flightStatus";
import type { QuickFilter } from "./utils/flightStatus";
import { ForecastChart } from "./components/forecast/ForecastChart";
import { useSectorFlights } from "./hooks/useSectorFlights";
import { useSectorSocket } from "./hooks/useSectorSocket";
import { useSession } from "./hooks/useSession";
import { horaUtc, hoyIso } from "./utils/utcDate";
import type { Flight, Position, Sector, Shift } from "./types";

// El gráfico NO se carga aparte a propósito, aunque arrastre recharts. Cargado
// con lazy(), abrirlo costaba 535 ms: la resolución del Suspense sola se comía
// 225 ms aunque el paquete ya estuviera descargado. En el arranque esos 104 KB
// (ya comprimidos) se pagan una vez por sesión; el gráfico se abre muchas.
const UploadItinerary = lazy(() => import("./components/itinerary/UploadItinerary").then((m) => ({ default: m.UploadItinerary })));
const ImportFlightHistory = lazy(() =>
  import("./components/flights/ImportFlightHistory").then((m) => ({ default: m.ImportFlightHistory }))
);

type Tab = "grid-own" | "grid-other" | "bitacora" | "grafico" | "carga" | "importar-historico";

function otherSectorOf(sector: Sector): Sector {
  return sector === "SUR" ? "NOR" : "SUR";
}

// Misma regla que Login.tsx: FMP CUSCO comparte los datos del sector SUR,
// así que un turno abierto en esa posición también "ocupa" el sector SUR.
function positionCoversSector(position: Position, sector: Sector): boolean {
  return sector === "NOR" ? position === "FMP NOR" : position !== "FMP NOR";
}

export default function App() {
  return (
    <AppProvider>
      <AppShell />
    </AppProvider>
  );
}

function AppShell() {
  const { session, checking: checkingSession, loginBusy, loginError, login, logout } = useSession();

  const [tab, setTab] = useState<Tab>("grid-own");
  const [flightDate, setFlightDate] = useState(hoyIso());
  const {
    flights,
    loading: flightsLoading,
    loadFlights,
    upsertFlight: upsertFlightLocally,
    removeFlight: removeFlightLocally,
  } = useSectorFlights(session?.sector ?? null, flightDate);
  const {
    flights: otherFlights,
    loading: otherFlightsLoading,
    loadFlights: loadOtherFlights,
    upsertFlight: upsertOtherFlightLocally,
    removeFlight: removeOtherFlightLocally,
  } = useSectorFlights(session ? otherSectorOf(session.sector) : null, flightDate);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<QuickFilter>("all");
  const [otherFilter, setOtherFilter] = useState<QuickFilter>("all");
  // Modo cobertura: excepcional, para cuando queda un solo operador FMP
  // atendiendo las dos zonas (ej. el otro fue al baño). Se activa a mano
  // cada sesión -- nunca se guarda en localStorage -- para que no quede
  // prendido por accidente en la sesión de otro turno. Cada cambio que se
  // haga en la zona ajena queda igual en el historial del vuelo con el
  // nombre real de quien lo hizo (FlightHistory.operator_name), así que no
  // hace falta un registro aparte para saber quién editó qué.
  const [coverageMode, setCoverageMode] = useState(false);
  const [coveragePopoverOpen, setCoveragePopoverOpen] = useState(false);
  // Nombres con turno abierto ahora mismo en la posición de la zona ajena
  // (puede haber más de uno: FMP SUR + FMP CUSCO comparten sector SUR).
  const [otherActiveOperators, setOtherActiveOperators] = useState<string[]>([]);
  // Perfiles de solo lectura (DGAC) con turno abierto: los operadores FMP
  // tienen derecho a saber quién está mirando su trabajo en vivo.
  const [activeObservers, setActiveObservers] = useState<string[]>([]);
  const [coverageToast, setCoverageToast] = useState<string | null>(null);
  const [handover, setHandover] = useState<Shift | null>(null);

  const { setPresence, clearPresenceFor, refreshReasonCodes } = useAppContext();

  const ownCounts = useMemo(() => computeQuickFilterCounts(flights), [flights]);
  const otherCounts = useMemo(() => computeQuickFilterCounts(otherFlights), [otherFlights]);
  // Regla del área: los días que ya pasaron no se modifican (solo lectura),
  // salvo casos excepcionales -- que son muy raros y se resuelven a mano
  // directo en la base de datos, no desde la interfaz.
  const isPastDate = flightDate < hoyIso();
  // Perfil genérico de solo lectura (ej. DGAC): ve las dos zonas en vivo,
  // pero ningún control de edición se muestra y el backend además rechaza
  // cualquier escritura sin importar qué endpoint la pida (ver
  // require_writable_shift en el backend) -- esto es solo para no mostrar
  // botones que de todas formas van a fallar.
  const isViewer = session?.shift.read_only ?? false;

  useEffect(() => {
    if (session) loadFlights();
  }, [session, flightDate, loadFlights]);

  useEffect(() => {
    if (session) loadOtherFlights();
  }, [session, flightDate, loadOtherFlights]);

  // Quién tiene abierta la posición de la zona ajena ahora mismo -- se
  // muestra en la pestaña para saber a quién se está reemplazando, y sirve
  // de aviso de que el otro operador FMP ya volvió (ver manejo de
  // "shift_started" en el WS más abajo).
  const refreshOtherActiveOperators = useCallback(async (sector: Sector) => {
    try {
      const active = await api.activeShifts();
      setOtherActiveOperators(active.filter((s) => positionCoversSector(s.position, sector)).map((s) => s.operator_name));
      setActiveObservers(active.filter((s) => s.read_only).map((s) => s.operator_name));
    } catch {
      // si falla, se mantiene lo que había -- no es crítico
    }
  }, []);

  useEffect(() => {
    if (session) refreshOtherActiveOperators(otherSectorOf(session.sector));
  }, [session, refreshOtherActiveOperators]);

  // Turno excedido: el estado lo decide el servidor (campo excede_maximo, ver
  // auth.SHIFT_MAX_HOURS) y no se calcula acá, para que el aviso no dependa
  // de cómo tenga la hora esta computadora. Cada 5 min es más que suficiente
  // para un umbral medido en horas.
  const [ownShift, setOwnShift] = useState<Shift | null>(null);
  useEffect(() => {
    if (!session) {
      setOwnShift(null);
      return;
    }
    const shiftId = session.shift.id;
    let cancelado = false;
    async function revisar() {
      try {
        const active = await api.activeShifts();
        if (!cancelado) setOwnShift(active.find((s) => s.id === shiftId) ?? null);
      } catch {
        // si falla se mantiene lo último conocido -- no es crítico
      }
    }
    revisar();
    const t = setInterval(revisar, 5 * 60_000);
    return () => {
      cancelado = true;
      clearInterval(t);
    };
  }, [session]);

  // Nota de relevo: al entrar a la posición se muestra la nota que dejó el
  // turno anterior (últimas 24 h). Una vez leída, no vuelve a aparecer.
  useEffect(() => {
    if (!session || session.shift.read_only) return;
    (async () => {
      try {
        const last = await api.lastHandover(session.shift.position);
        if (
          last?.handover_note &&
          last.id !== session.shift.id &&
          localStorage.getItem(`ctot_handover_seen_${last.id}`) !== "1"
        ) {
          setHandover(last);
        }
      } catch {
        // sin nota disponible: no es un error para el operador
      }
    })();
  }, [session]);

  useEffect(() => {
    if (!coverageToast) return;
    const t = setTimeout(() => setCoverageToast(null), 8000);
    return () => clearTimeout(t);
  }, [coverageToast]);

  function dismissHandover() {
    if (handover) localStorage.setItem(`ctot_handover_seen_${handover.id}`, "1");
    setHandover(null);
  }

  function toggleCoverageMode() {
    if (!session) return;
    if (coverageMode) {
      setCoverageMode(false);
      return;
    }
    setCoveragePopoverOpen(true);
  }

  function confirmCoverageMode() {
    setCoveragePopoverOpen(false);
    setCoverageMode(true);
  }

  const sendWs = useSectorSocket(
    session?.sector ?? "SUR",
    (msg) => {
      if (!session) return;
      if (msg.type === "flight_upserted") {
        // El backend hace broadcast a todas las conexiones sin filtrar por
        // sector, así que un mismo evento puede ser del sector propio o del
        // ajeno -- se enruta según flight.sector (no hace falta un segundo WS).
        const flight = msg.flight as Flight;
        if (flight.sector === session.sector) upsertFlightLocally(flight);
        else upsertOtherFlightLocally(flight);
      } else if (msg.type === "flight_deleted") {
        // flight_deleted no trae sector -- se intenta quitar de ambos
        // arreglos (es un no-op en el que no lo tenga).
        removeFlightLocally(msg.id);
        removeOtherFlightLocally(msg.id);
      } else if (msg.type === "presence") {
        if (msg.operator_name === session.operatorName) return; // no reflejar mi propio foco
        clearPresenceFor(msg.operator_name);
        if (msg.flight_id != null) setPresence(msg.flight_id, msg.operator_name, msg.sector);
      } else if (msg.type === "shift_started" || msg.type === "shift_ended") {
        const shift = msg.shift as Shift;
        if (shift.read_only) {
          // Un observador no cubre una posición: mira las dos, así que su
          // entrada/salida se refleja sin filtrar por sector.
          setActiveObservers((prev) =>
            msg.type === "shift_started"
              ? prev.includes(shift.operator_name) ? prev : [...prev, shift.operator_name]
              : prev.filter((n) => n !== shift.operator_name),
          );
          return;
        }
        const other = otherSectorOf(session.sector);
        if (!positionCoversSector(shift.position, other)) return;
        refreshOtherActiveOperators(other);
        // Si alguien inicia turno en la posición que se estaba cubriendo,
        // es la señal más clara de que el otro operador FMP ya volvió --
        // se devuelve la zona a solo lectura sola y se avisa con un toast
        // en vez de dejarlo prendido a la espera de que alguien se acuerde.
        if (msg.type === "shift_started" && coverageMode) {
          setCoverageMode(false);
          setCoverageToast(`Modo cobertura desactivado: ${shift.operator_name} inició turno en FMP ${other}.`);
        }
      }
    },
    () => {
      // El WS estuvo caído y se reconectó: pudieron perderse cambios
      // (ej. un vuelo borrado por otra estación) -- se recarga la grilla
      // en vez de arrastrar filas desincronizadas del servidor.
      if (session) {
        loadFlights();
        loadOtherFlights();
      }
    },
    // Sin turno no hay token: conectar desde el login solo produce
    // handshakes rechazados y demora la sincronización al entrar.
    session !== null,
  );

  // El catálogo de motivos (SEC/REV) se pide recién con turno abierto: el
  // endpoint pasó a exigir token, y antes se pedía al cargar la página.
  useEffect(() => {
    if (!session) return;
    refreshReasonCodes().catch(() => {
      // Si falla, los desplegables quedan vacíos pero la grilla sigue usable;
      // el siguiente refresco lo vuelve a intentar.
    });
  }, [session, refreshReasonCodes]);

  function handleFocusFlight(flightId: number | null) {
    if (!session) return;
    sendWs({ type: "focus", flight_id: flightId, operator_name: session.operatorName, sector: session.sector });
  }

  // El perfil Observador no gestiona ninguna posición, así que no tiene
  // sentido pedirle una nota de relevo (esa nota es para el próximo
  // operador FMP de esa posición) -- cierra turno y vuelve al login directo.
  async function handleViewerExit() {
    if (!session) return;
    try {
      await api.clockOut(session.shift.id);
    } finally {
      logout();
    }
  }

  if (checkingSession) {
    return null;
  }

  if (!session) {
    return <Login onLogin={login} loading={loginBusy} error={loginError} />;
  }

  return (
    <div className="app-shell">
      <AppHeader
        session={session}
        isViewer={isViewer}
        search={search}
        onSearchChange={setSearch}
        flightDate={flightDate}
        onFlightDateChange={setFlightDate}
        activeObservers={activeObservers}
        onViewerExit={handleViewerExit}
        onError={setCoverageToast}
      />

      <nav className="app-tabs" aria-label="Secciones">
        {!isViewer && (
          <NavMenu
            activeTab={tab}
            onSelect={setTab}
            sector={session.sector}
            flightDate={flightDate}
            canExport={session.shift.can_export !== false}
            quickFilters={
              tab === "grid-own"
                ? { counts: ownCounts, filter, onChange: setFilter }
                : tab === "grid-other"
                  ? { counts: otherCounts, filter: otherFilter, onChange: setOtherFilter }
                  : undefined
            }
          />
        )}
        <button className={tab === "grid-own" ? "active" : ""} onClick={() => setTab("grid-own")}>
          FMP {session.sector}
        </button>
        {isViewer ? (
          <button className={tab === "grid-other" ? "active" : ""} onClick={() => setTab("grid-other")}>
            FMP {otherSectorOf(session.sector)}
          </button>
        ) : (
          <div className={`app-tab-group${tab === "grid-other" ? " active" : ""}${coverageMode ? " coverage-on" : ""}`}>
            <button className={`app-tab-main${tab === "grid-other" ? " active" : ""}`} onClick={() => setTab("grid-other")}>
              <span>FMP {otherSectorOf(session.sector)}</span>
              <span className="app-tab-sub">
                {otherActiveOperators.length > 0 ? otherActiveOperators.join(", ") : "Sin turno abierto"}
              </span>
            </button>
            <button
              className={`coverage-icon-btn${coverageMode ? " active" : ""}`}
              onClick={toggleCoverageMode}
              title={
                coverageMode
                  ? `Modo cobertura activo: podés editar FMP ${otherSectorOf(session.sector)}. Click para desactivarlo.`
                  : `Activar modo cobertura (excepcional): gestionar también FMP ${otherSectorOf(session.sector)} cuando queda un solo operador FMP para las dos zonas.`
              }
              aria-label="Modo cobertura"
            >
              <ShieldIcon />
            </button>
          </div>
        )}
        <div className="tab-spacer" />
        {!isViewer && <MiniForecast flightDate={flightDate} onOpenFull={() => setTab("grafico")} />}
      </nav>

      <main className="app-main">
        {ownShift?.excede_maximo && (
          <div className="shift-excess-banner" role="alert">
            <div className="shift-excess-body">
              <span className="shift-excess-title">Turno excedido</span>
              <p className="shift-excess-note">
                {ownShift.operator_name}, tu turno en {ownShift.position} lleva{" "}
                <strong>{Math.floor(ownShift.horas_en_turno)} h {Math.round((ownShift.horas_en_turno % 1) * 60)} min</strong>{" "}
                abierto, por encima del máximo de {ownShift.maximo_horas} h por posición.
                Si ya terminaste, cerralo para que quede registrado en la bitácora.
              </p>
            </div>
            <button className="shift-excess-action" onClick={() => setTab("bitacora")}>
              Ir a cerrar turno
            </button>
          </div>
        )}
        {!isViewer && handover && (
          <div className="handover-banner" role="alert">
            <div className="handover-banner-body">
              <span className="handover-banner-title">
                Nota de relevo · {handover.operator_name} · {handover.position} ·{" "}
                {handover.end_time ? horaUtc(handover.end_time) : ""}{" "}
                UTC
              </span>
              <p className="handover-banner-note">{handover.handover_note}</p>
            </div>
            <button className="handover-banner-ack" onClick={dismissHandover}>
              Realizado
            </button>
          </div>
        )}
        <div className="view" key={tab}>
          {tab === "grid-own" && (
            <FlightGrid
              sector={session.sector}
              flightDate={flightDate}
              flights={flights}
              loading={flightsLoading}
              search={search}
              filter={filter}
              onFilterChange={setFilter}
              readOnly={isViewer || isPastDate}
              readOnlyReason={
                isViewer
                  ? "Solo lectura — este perfil no puede modificar datos."
                  : "Solo lectura — los registros de días anteriores no se modifican."
              }
              onFlightUpserted={upsertFlightLocally}
              onFlightDeleted={removeFlightLocally}
              onFocusFlight={isViewer ? () => {} : handleFocusFlight}
            />
          )}
          {tab === "grid-other" && (
            <FlightGrid
              sector={otherSectorOf(session.sector)}
              flightDate={flightDate}
              flights={otherFlights}
              loading={otherFlightsLoading}
              search={search}
              filter={otherFilter}
              onFilterChange={setOtherFilter}
              readOnly={isViewer || !coverageMode || isPastDate}
              readOnlyReason={
                isViewer
                  ? "Solo lectura — este perfil no puede modificar datos."
                  : isPastDate
                    ? "Solo lectura — los registros de días anteriores no se modifican."
                    : "Solo lectura — no gestionás esta zona. Activá el modo cobertura arriba si sos el único operador FMP disponible."
              }
              onFlightUpserted={upsertOtherFlightLocally}
              onFlightDeleted={removeOtherFlightLocally}
              onFocusFlight={!isViewer && coverageMode ? handleFocusFlight : () => {}}
            />
          )}
          {tab === "bitacora" && (
            <BitacoraPanel currentShiftId={session.shift.id} onClockOut={logout} />
          )}
          {tab === "grafico" && (
            <Suspense fallback={<div className="pane-loading">Cargando gráfico…</div>}>
              <ForecastChart flightDate={flightDate} />
            </Suspense>
          )}
          {tab === "carga" && (
            <Suspense fallback={<div className="pane-loading">Cargando…</div>}>
              <UploadItinerary defaultDate={flightDate} />
            </Suspense>
          )}
          {tab === "importar-historico" && (
            <Suspense fallback={<div className="pane-loading">Cargando…</div>}>
              <ImportFlightHistory defaultDate={flightDate} defaultSector={session.sector} />
            </Suspense>
          )}
        </div>
      </main>

      {coveragePopoverOpen && (
        <CoveragePopover
          sector={otherSectorOf(session.sector)}
          operatorName={session.operatorName}
          activeOperators={otherActiveOperators}
          onConfirm={confirmCoverageMode}
          onClose={() => setCoveragePopoverOpen(false)}
        />
      )}

      {coverageToast && (
        <div className="coverage-toast" role="status">
          <span className="coverage-toast-icon"><ShieldIcon /></span>
          <p>{coverageToast}</p>
          <button onClick={() => setCoverageToast(null)} aria-label="Cerrar aviso"><CloseIcon /></button>
        </div>
      )}
    </div>
  );
}
