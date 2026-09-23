import { useState } from "react";
import corpacLogo from "../../assets/corpac-logo.png";
import { useAppContext } from "../../contexts/AppContext";
import { api, ApiError } from "../../services/api";
import type { Session } from "../../hooks/useSession";
import { DownloadIcon, EyeIcon, LogOutIcon, MoonIcon, SunIcon } from "./Icons";
import { UtcClock } from "./UtcClock";

interface Props {
  session: Session;
  isViewer: boolean;
  search: string;
  onSearchChange: (valor: string) => void;
  flightDate: string;
  onFlightDateChange: (valor: string) => void;
  /** Nombres de perfiles de consulta (DGAC) con turno abierto ahora mismo. */
  activeObservers: string[];
  onViewerExit: () => void;
  onError: (mensaje: string) => void;
}

export function AppHeader({
  session,
  isViewer,
  search,
  onSearchChange,
  flightDate,
  onFlightDateChange,
  activeObservers,
  onViewerExit,
  onError,
}: Props) {
  const { theme, toggleTheme } = useAppContext();
  const [exporting, setExporting] = useState(false);

  async function descargarCsv() {
    setExporting(true);
    try {
      await api.downloadExport(flightDate);
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "No se pudo descargar el CSV");
    } finally {
      setExporting(false);
    }
  }

  return (
    <header className="app-header">
      <div className="header-left">
        <div className="brand">
          <span className="brand-logo-plate">
            <img src={corpacLogo} alt="CORPAC" className="brand-logo" />
          </span>
          <span className="brand-name">FMP LIMA <span className="brand-sep">-</span> GDP</span>
        </div>
        <span className="operator-badge">
          <span className="live-dot" aria-hidden="true" />
          {session.operatorName}
          <span className="operator-sector">FMP {session.sector}</span>
        </span>
      </div>

      <UtcClock />

      <div className="header-right">
        <input
          className="global-search"
          type="search"
          placeholder="Buscar vuelo…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
        />
        <input
          className="header-date"
          type="date"
          value={flightDate}
          onChange={(e) => onFlightDateChange(e.target.value)}
        />
        <button
          className="theme-toggle"
          onClick={toggleTheme}
          title={theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
          aria-label={theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
        {!isViewer && activeObservers.length > 0 && (
          <span
            className="observer-watch"
            title={`Perfil${activeObservers.length > 1 ? "es" : ""} de consulta con turno abierto: ${activeObservers.join(", ")}. Ven la grilla en vivo, no pueden modificarla.`}
          >
            <EyeIcon />
            {activeObservers.length === 1 ? activeObservers[0] : `${activeObservers.length} observando`}
          </span>
        )}
        {isViewer && session.shift.can_export !== false && (
          <button
            className="viewer-export-btn"
            disabled={exporting}
            title={
              session.shift.export_max_weeks
                ? `Descargar el CSV del día en pantalla (hasta ${session.shift.export_max_weeks} semanas hacia atrás)`
                : "Descargar el CSV del día en pantalla"
            }
            onClick={descargarCsv}
          >
            <DownloadIcon /> {exporting ? "Descargando…" : "Descargar CSV"}
          </button>
        )}
        {isViewer && (
          <button className="viewer-exit-btn" onClick={onViewerExit} title="Salir del perfil Observador">
            <LogOutIcon /> Salir
          </button>
        )}
      </div>
    </header>
  );
}
