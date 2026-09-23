import { useEffect, useRef, useState } from "react";
import { api } from "../../services/api";
import { fechaHoraUtc } from "../../utils/utcDate";
import type {
  FlightHistoryImportEntry,
  FlightHistoryImportReport,
  FlightHistoryPreview,
  Sector,
} from "../../types";

interface Props {
  defaultDate: string;
  defaultSector: Sector;
}

// Columnas de la tabla de vista previa: las mismas de la grilla, en el mismo
// orden. Se muestran todas las que traigan al menos un dato en el archivo.
const PREVIEW_COLUMNS: { key: string; label: string }[] = [
  { key: "hora", label: "HORA" },
  { key: "vuelo", label: "VUELO" },
  { key: "adep", label: "ADEP" },
  { key: "dep", label: "DEP" },
  { key: "eta_aircon", label: "ETA AIRCON" },
  { key: "eta_aircon_calculado", label: "ETA CALC." },
  { key: "slot_arr_dgac", label: "SLOT ARR DGAC" },
  { key: "etd1", label: "ETD 1" },
  { key: "ctot1", label: "CTOT 1" },
  { key: "sec1", label: "SEC1" },
  { key: "h_rev1", label: "H. REV1" },
  { key: "rev1", label: "REV1" },
  { key: "etd2", label: "ETD 2" },
  { key: "ctot2", label: "CTOT 2" },
  { key: "sec2", label: "SEC2" },
  { key: "etd3", label: "ETD 3" },
  { key: "ctot3", label: "CTOT 3" },
  { key: "sec3", label: "SEC3" },
  { key: "etd4", label: "ETD 4" },
  { key: "ctot4", label: "CTOT 4" },
  { key: "sec4", label: "SEC4" },
  { key: "dla_minutos", label: "DLA (min)" },
  { key: "observaciones", label: "OBSERVACIONES" },
];

/** Los dos sectores salen del MISMO libro: el CONSOLIDADO trae una hoja por
 *  cada uno. Hacer el recorrido completo dos veces por el mismo archivo era
 *  trabajo duplicado sin motivo. Se procesan en dos cargas y no en una: el
 *  modelo registra una importación por sector, y así cada grilla queda
 *  apuntando a la suya. */
type SectorElegido = Sector | "AMBOS";

const SECTORES_DE: Record<SectorElegido, Sector[]> = {
  SUR: ["SUR"],
  NOR: ["NOR"],
  AMBOS: ["SUR", "NOR"],
};

interface VistaPreviaDeSector {
  sector: Sector;
  datos: FlightHistoryPreview;
}

interface ResultadoDeSector {
  sector: Sector;
  datos: FlightHistoryImportReport;
}

export function ImportFlightHistory({ defaultDate, defaultSector }: Props) {
  const [sector, setSector] = useState<SectorElegido>(defaultSector);
  const [flightDate, setFlightDate] = useState(defaultDate);
  const [previews, setPreviews] = useState<VistaPreviaDeSector[] | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [reports, setReports] = useState<ResultadoDeSector[] | null>(null);
  // Pisar lo ya cargado se pide a propósito, igual que la carga de itinerario
  // exige confirmar: es destructivo y no puede pasar por descuido.
  const [reemplazar, setReemplazar] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [imports, setImports] = useState<FlightHistoryImportEntry[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  async function refreshImports() {
    try {
      setImports(await api.flightHistoryImports());
    } catch {
      // el historial es informativo: si falla, no bloquea la carga
    }
  }

  useEffect(() => {
    refreshImports();
  }, []);

  // Paso 1: al elegir el archivo solo se pide la VISTA PREVIA (no guarda nada).
  async function handleFile(file: File) {
    setBusy(true);
    setError(null);
    setReports(null);
    setPreviews(null);
    setPendingFile(null);
    setReemplazar(false);
    try {
      const resultados: VistaPreviaDeSector[] = [];
      for (const s of SECTORES_DE[sector]) {
        resultados.push({ sector: s, datos: await api.previewFlightHistory(s, flightDate, file) });
      }
      setPreviews(resultados);
      setPendingFile(file);
    } catch (e: any) {
      setError(e.message ?? "No se pudo procesar el archivo");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  // Paso 2: "Procesar" confirma e inserta lo que se mostró en la vista previa.
  async function handleProcess() {
    if (!pendingFile || !previews) return;
    setBusy(true);
    setError(null);
    // Se acumula sobre la marcha: si el segundo sector falla, el resultado del
    // primero igual se muestra. Ya está guardado -- ocultarlo haría creer que
    // no entró nada y llevaría a recargarlo por duplicado.
    const hechos: ResultadoDeSector[] = [];
    try {
      for (const { sector: s } of previews) {
        hechos.push({
          sector: s,
          datos: await api.importFlightHistory(s, flightDate, pendingFile, reemplazar),
        });
      }
      setPreviews(null);
      setPendingFile(null);
    } catch (e: any) {
      setError(e.message ?? "No se pudo importar el archivo");
    } finally {
      if (hechos.length > 0) setReports(hechos);
      setBusy(false);
      refreshImports();
    }
  }

  function handleDiscard() {
    setPreviews(null);
    setPendingFile(null);
    setError(null);
    setReemplazar(false);
  }

  const totalAceptadas = previews?.reduce((n, p) => n + p.datos.filas_aceptadas, 0) ?? 0;
  const totalExistentes = previews?.reduce((n, p) => n + p.datos.vuelos_existentes, 0) ?? 0;

  // Solo columnas que traen al menos un dato (el CSV viejo suele dejar
  // muchas vacías; mostrarlas todas haría ilegible la vista previa). Se
  // calcula por sector: cada grilla llena columnas distintas.
  function columnasVisibles(datos: FlightHistoryPreview) {
    return PREVIEW_COLUMNS.filter(({ key }) =>
      datos.rows.some((row) => {
        const value = (row as unknown as Record<string, unknown>)[key];
        return value !== null && value !== undefined && value !== "";
      })
    );
  }

  return (
    <div className="upload-panel">
      <h2>Importar vuelos históricos</h2>
      <p className="upload-hint">
        Carga vuelos ya operados (con su CTOT) de días anteriores, un archivo por día:
        el <strong>CONSOLIDADO</strong> del día (.xlsx) o el CSV exportado de la grilla.
        Del libro se toma la hoja del sector que elijas acá, o las dos si elegís{" "}
        <strong>Ambos</strong>. Primero vas a ver una <strong>vista previa</strong> de
        todo lo que trae el archivo; nada se guarda hasta que aprietes{" "}
        <strong>Procesar importación</strong>.
      </p>

      <div className="upload-effective-from" style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        <label>
          Sector
          <select
            value={sector}
            onChange={(e) => setSector(e.target.value as SectorElegido)}
            disabled={previews !== null}
          >
            <option value="SUR">SUR</option>
            <option value="NOR">NOR</option>
            <option value="AMBOS">Ambos (SUR y NOR)</option>
          </select>
        </label>
        <label>
          Fecha del vuelo (día que cubre el archivo)
          <input
            type="date"
            value={flightDate}
            onChange={(e) => setFlightDate(e.target.value)}
            disabled={previews !== null}
          />
        </label>
      </div>

      {!previews && (
        <div
          className={`upload-dropzone ${dragOver ? "upload-dropzone-active" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const file = e.dataTransfer.files?.[0];
            if (file) handleFile(file);
          }}
          onClick={() => inputRef.current?.click()}
        >
          {busy ? "Leyendo el archivo…" : "Arrastra el archivo aquí o haz clic para elegirlo"}
          <input
            ref={inputRef}
            type="file"
            // El CONSOLIDADO del dia es lo que la FMP archiva; el CSV existe
            // solo si alguien exporto una hoja aparte. Con accept=".csv" el
            // selector escondia justo el archivo que el operador tiene.
            accept=".xlsx,.csv"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFile(file);
            }}
          />
        </div>
      )}

      {error && <p className="upload-error">{error}</p>}

      {previews && (
        <div className="upload-report">
          <p>
            Vista previa de <strong>{pendingFile?.name}</strong> —{" "}
            <strong>{totalAceptadas}</strong> vuelos listos para importar el{" "}
            <strong>{flightDate}</strong>
          </p>

          {totalExistentes > 0 && (
            <div className="upload-reemplazo">
              <p className="upload-error">
                ⚠ Ese día ya tiene {totalExistentes} vuelo(s) cargados
                {previews.length > 1 && (
                  <> ({previews.map((p) => `${p.sector}: ${p.datos.vuelos_existentes}`).join(", ")})</>
                )}
                .
              </p>
              <label className="upload-reemplazo-check">
                <input
                  type="checkbox"
                  checked={reemplazar}
                  onChange={(e) => setReemplazar(e.target.checked)}
                  disabled={busy}
                />
                <span>
                  <strong>Reemplazar lo que ya está cargado</strong> por lo que trae este
                  archivo. Los vuelos actuales de ese día y sector se borran; queda
                  constancia de cada uno, con quién lo hizo y cuándo, en el registro de
                  eliminaciones.
                </span>
              </label>
            </div>
          )}

          {previews.map(({ sector: s, datos }) => {
            const columnas = columnasVisibles(datos);
            return (
              <div key={s} className="upload-preview-sector">
                <h3>
                  {s} — <strong>{datos.filas_aceptadas}</strong> vuelos
                  {datos.filas_rechazadas > 0 && (
                    <> · <strong>{datos.filas_rechazadas}</strong> filas con error (no se importarán)</>
                  )}
                </h3>

                {datos.errores.length > 0 && (
                  <table className="upload-errors-table">
                    <thead><tr><th>Fila</th><th>Motivo</th></tr></thead>
                    <tbody>
                      {datos.errores.map((e, i) => (
                        <tr key={i}><td>{e.row}</td><td>{e.motivo}</td></tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <div style={{ overflowX: "auto", marginTop: "0.75rem" }}>
                  <table className="upload-history-table">
                    <thead>
                      <tr>
                        <th>N°</th>
                        {columnas.map(({ key, label }) => (
                          <th key={key}>{label}</th>
                        ))}
                        <th>CANCELADO</th>
                      </tr>
                    </thead>
                    <tbody>
                      {datos.rows.map((row, i) => (
                        <tr key={i}>
                          <td>{i + 1}</td>
                          {columnas.map(({ key }) => (
                            <td key={key}>{String((row as unknown as Record<string, unknown>)[key] ?? "")}</td>
                          ))}
                          <td>{row.cancelado ? "SÍ" : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}

          <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
            <button
              className="upload-process-btn"
              onClick={handleProcess}
              disabled={busy || totalAceptadas === 0 || (totalExistentes > 0 && !reemplazar)}
            >
              {busy
                ? "Procesando…"
                : totalExistentes > 0 && reemplazar
                  ? `Reemplazar e importar (${totalAceptadas} vuelos)`
                  : `Procesar importación (${totalAceptadas} vuelos)`}
            </button>
            <button className="upload-discard-btn" onClick={handleDiscard} disabled={busy}>
              Descartar
            </button>
          </div>
        </div>
      )}

      {reports && (
        <div className="upload-report">
          {reports.map(({ sector: s, datos }) => (
            <div key={s}>
              <p>
                ✔ {s}: <strong>{datos.filas_aceptadas}</strong> vuelos insertados
                {datos.vuelos_reemplazados > 0 && (
                  <> · <strong>{datos.vuelos_reemplazados}</strong> reemplazados</>
                )}
                {datos.filas_rechazadas > 0 && (
                  <> · <strong>{datos.filas_rechazadas}</strong> filas rechazadas</>
                )}
              </p>
              {datos.errores.length > 0 && (
                <table className="upload-errors-table">
                  <thead><tr><th>Fila</th><th>Motivo</th></tr></thead>
                  <tbody>
                    {datos.errores.map((e, i) => (
                      <tr key={i}><td>{e.row}</td><td>{e.motivo}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
          <p className="upload-hint upload-hint-small">
            Cambiá la fecha de la grilla para verlos.
          </p>
        </div>
      )}

      <section className="upload-history">
        <h3>Importaciones recibidas</h3>
        {imports.length === 0 && (
          <p className="upload-hint upload-hint-small">Todavía no se importó ningún histórico.</p>
        )}
        {imports.length > 0 && (
          <table className="upload-history-table">
            <thead>
              <tr>
                <th>Cargado (UTC)</th>
                <th>Sector</th>
                <th>Fecha del vuelo</th>
                <th>Archivo</th>
                <th>Filas</th>
                <th>Cargó</th>
              </tr>
            </thead>
            <tbody>
              {imports.map((u) => (
                <tr key={u.id}>
                  <td>{fechaHoraUtc(u.uploaded_at, true)}</td>
                  <td>{u.sector}</td>
                  <td>{u.flight_date}</td>
                  <td className="upload-filename" title={u.filename ?? undefined}>{u.filename ?? "—"}</td>
                  <td>
                    {u.filas_aceptadas}
                    {u.filas_rechazadas > 0 && <span className="upload-rejected"> · {u.filas_rechazadas} rech.</span>}
                  </td>
                  <td>{u.uploaded_by ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
