import { useEffect, useRef, useState } from "react";
import { ZOOM_STEPS, useAppContext } from "../../contexts/AppContext";
import { api, ApiError } from "../../services/api";
import type { QuickFilter, QuickFilterCounts } from "../../utils/flightStatus";
import type { Sector } from "../../types";
import { DownloadIcon, MenuIcon } from "./Icons";

export type NavMenuTab = "bitacora" | "grafico" | "carga" | "importar-historico";

const ITEMS: { tab: NavMenuTab; label: string }[] = [
  { tab: "bitacora", label: "Bitácora" },
  { tab: "grafico", label: "Gráfico" },
  { tab: "carga", label: "Itinerario" },
  { tab: "importar-historico", label: "Importar histórico" },
];

const FILTER_ITEMS: { filter: QuickFilter; label: string }[] = [
  { filter: "all", label: "Todos" },
  { filter: "alert", label: "Con alerta" },
  { filter: "cancelled", label: "Cancelados" },
  { filter: "noItin", label: "Sin itinerario" },
];

interface QuickFiltersProp {
  counts: QuickFilterCounts;
  filter: QuickFilter;
  onChange: (filter: QuickFilter) => void;
}

interface Props {
  activeTab: string;
  onSelect: (tab: NavMenuTab) => void;
  sector: Sector;
  flightDate: string;
  /* Si el perfil no descarga (ej. el segundo de la DGAC), la opción no se
     muestra: es más honesto que ofrecerla y devolver un error al tocarla. */
  canExport?: boolean;
  quickFilters?: QuickFiltersProp;
}

/** Agrupa las secciones de uso ocasional (Bitácora, Gráfico, Itinerario) más
 * el tamaño de letra, filtros rápidos y exportar CSV, para que la barra
 * principal quede solo con las pestañas FMP SUR/FMP NOR y "Pendientes"
 * (el filtro de mayor uso queda a mano en la grilla). */
export function NavMenu({ activeTab, onSelect, sector, flightDate, canExport = true, quickFilters }: Props) {
  const [exportError, setExportError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { zoom, setZoom } = useAppContext();

  const zoomIdx = ZOOM_STEPS.indexOf(zoom as (typeof ZOOM_STEPS)[number]);
  const zoomOut = () => zoomIdx > 0 && setZoom(ZOOM_STEPS[zoomIdx - 1]);
  const zoomIn = () => zoomIdx < ZOOM_STEPS.length - 1 && setZoom(ZOOM_STEPS[zoomIdx + 1]);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="nav-menu" ref={rootRef}>
      <button
        className={`nav-menu-trigger${open ? " active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Más secciones"
        aria-label="Más secciones"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MenuIcon />
      </button>
      {open && (
        <div className="nav-menu-panel" role="menu">
          {ITEMS.map((item) => (
            <button
              key={item.tab}
              className={`nav-menu-item${activeTab === item.tab ? " active" : ""}`}
              role="menuitem"
              onClick={() => {
                onSelect(item.tab);
                setOpen(false);
              }}
            >
              {item.label}
            </button>
          ))}

          {quickFilters && (
            <>
              <div className="nav-menu-separator" role="separator" />
              {FILTER_ITEMS.map((item) => (
                <button
                  key={item.filter}
                  className={`nav-menu-item nav-menu-filter${quickFilters.filter === item.filter ? " active" : ""}`}
                  role="menuitemradio"
                  aria-checked={quickFilters.filter === item.filter}
                  onClick={() => {
                    quickFilters.onChange(item.filter);
                    setOpen(false);
                  }}
                >
                  {item.label} <span className="count">{quickFilters.counts[item.filter]}</span>
                </button>
              ))}
            </>
          )}

          <div className="nav-menu-separator" role="separator" />

          <div className="nav-menu-zoom" role="group" aria-label="Tamaño de la pantalla">
            <span className="nav-menu-zoom-label">Tamaño de letra</span>
            <div className="zoom-control">
              <button onClick={zoomOut} disabled={zoomIdx <= 0} title="Reducir el tamaño de la pantalla" aria-label="Reducir tamaño">
                A−
              </button>
              <span className="zoom-control-value">{Math.round(zoom * 100)}%</span>
              <button
                onClick={zoomIn}
                disabled={zoomIdx >= ZOOM_STEPS.length - 1}
                title="Ampliar la pantalla para ver más grande"
                aria-label="Ampliar tamaño"
              >
                A+
              </button>
            </div>
          </div>

          {canExport && (
            <button
              type="button"
              className="nav-menu-item nav-menu-export"
              role="menuitem"
              title="Descargar CSV del día para subir a la plataforma obligatoria"
              onClick={async () => {
                setExportError(null);
                try {
                  await api.downloadExport(flightDate, sector);
                  setOpen(false);
                } catch (e) {
                  // El backend puede rechazar la fecha (ej. fuera de la
                  // ventana de semanas de un perfil de consulta): se muestra
                  // el motivo en el propio menú en vez de fallar en silencio.
                  setExportError(e instanceof ApiError ? e.message : "No se pudo descargar el CSV");
                }
              }}
            >
              <DownloadIcon /> Exportar CSV
            </button>
          )}
          {exportError && <p className="nav-menu-export-error">{exportError}</p>}
        </div>
      )}
    </div>
  );
}
