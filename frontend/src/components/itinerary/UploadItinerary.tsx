import { useEffect, useRef, useState } from "react";
import { api } from "../../services/api";
import { fechaHoraUtc } from "../../utils/utcDate";
import type {
  Estacion,
  ItineraryAlcance,
  ItineraryPreview,
  ItineraryUploadEntry,
  ItineraryUploadReport,
} from "../../types";

interface Props {
  defaultDate: string;
}

interface OpcionesCarga {
  desde: string;
  alcance: ItineraryAlcance;
  /** Hoja del libro a leer; null = la elige el backend. */
  hoja?: string | null;
  /** Aerodromo cuyo itinerario se reemplaza. */
  estacion: string;
}

// Lima, el unico aerodromo del sistema hasta 2026 y el que trae el itinerario
// todos los dias. Se preselecciona para que la carga de siempre siga siendo un
// clic; si el catalogo no lo tuviera, se toma el primero que haya.
const ESTACION_POR_DEFECTO = "SPJC";

const FORMATO_LABEL: Record<ItineraryPreview["formato"], string> = {
  season: "Itinerario de temporada (cada fila trae su propia fecha)",
  legacy: 'Carga diaria estilo hoja "INFO" (sin fecha propia)',
  csv: "CSV plano (sin fecha propia)",
};

export function UploadItinerary({ defaultDate }: Props) {
  const [effectiveFrom, setEffectiveFrom] = useState(defaultDate);
  const [alcance, setAlcance] = useState<ItineraryAlcance>("desde");
  // El libro de la DGAC trae varias hojas parciales. null = la elige el
  // backend (la que cubre la fecha con los datos mas sanos).
  const [hoja, setHoja] = useState<string | null>(null);
  // El archivo queda "en espera" con su vista previa: nada se aplica hasta
  // que el operador confirma. Antes se aplicaba con solo soltarlo.
  const [pending, setPending] = useState<File | null>(null);
  const [preview, setPreview] = useState<ItineraryPreview | null>(null);
  const [report, setReport] = useState<ItineraryUploadReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [uploads, setUploads] = useState<ItineraryUploadEntry[]>([]);
  const [estaciones, setEstaciones] = useState<Estacion[]>([]);
  const [estacion, setEstacion] = useState(ESTACION_POR_DEFECTO);
  const inputRef = useRef<HTMLInputElement>(null);

  async function refreshUploads() {
    try {
      setUploads(await api.itineraryUploads());
    } catch {
      // el historial es informativo: si falla, no bloquea la carga
    }
  }

  useEffect(() => {
    refreshUploads();
    (async () => {
      try {
        const lista = await api.estaciones();
        setEstaciones(lista);
        // Si Lima no estuviera en el catalogo, dejar seleccionado un codigo que
        // no existe haria que la carga fallara con un 422 dificil de entender.
        if (lista.length > 0 && !lista.some((e) => e.codigo_oaci === ESTACION_POR_DEFECTO)) {
          setEstacion(lista[0].codigo_oaci);
        }
      } catch {
        // El selector queda con Lima, que es el comportamiento de siempre.
      }
    })();
  }, []);

  // Paso 1: solo valida y muestra qué pasaría. No escribe nada.
  async function revisarArchivo(file: File, opts: OpcionesCarga) {
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      setPreview(
        await api.previewItinerary(opts.desde, file, {
          alcance: opts.alcance,
          hoja: opts.hoja ?? null,
          estacion: opts.estacion,
        })
      );
      setPending(file);
      setHoja(opts.hoja ?? null);
    } catch (e: any) {
      setPreview(null);
      setPending(null);
      setError(e.message ?? "No se pudo leer el archivo");
    } finally {
      setBusy(false);
    }
  }

  // Paso 2: recién acá se reemplaza el itinerario.
  async function aplicar() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.uploadItinerary(effectiveFrom, pending, {
        alcance,
        // La misma que se reviso: si difiriera, se estaria reemplazando el
        // itinerario de un aerodromo distinto del que quedo a la vista.
        estacion: preview?.estacion ?? estacion,
        // La hoja que quedo a la vista en la revision, no la que el backend
        // vuelva a elegir: se aplica exactamente lo que se reviso.
        hoja: hoja ?? preview?.hoja ?? null,
      });
      setReport(result);
      descartar();
      refreshUploads();
    } catch (e: any) {
      setError(e.message ?? "No se pudo aplicar la carga");
    } finally {
      setBusy(false);
    }
  }

  function descartar() {
    setPending(null);
    setPreview(null);
    setHoja(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  // Cualquiera de las opciones cambia qué itinerario se reemplaza, así que se
  // recalcula la vista previa con el mismo archivo en vez de dejar a la vista
  // un resumen que ya no corresponde.
  function cambiarFecha(nueva: string) {
    setEffectiveFrom(nueva);
    if (pending) revisarArchivo(pending, { desde: nueva, alcance, hoja, estacion });
  }

  function cambiarAlcance(nuevo: ItineraryAlcance) {
    setAlcance(nuevo);
    if (pending) {
      revisarArchivo(pending, {
        desde: effectiveFrom,
        alcance: nuevo,
        hoja,
        estacion,
      });
    }
  }

  // Cambiar de hoja cambia el itinerario entero, asi que se vuelve a revisar.
  function cambiarHoja(nueva: string) {
    const elegida = nueva || null;
    setHoja(elegida);
    if (pending) {
      revisarArchivo(pending, {
        desde: effectiveFrom,
        alcance,
        hoja: elegida,
        estacion,
      });
    }
  }

  // Cambiar de aerodromo cambia por completo lo que esta carga reemplaza, asi
  // que se vuelve a revisar igual que con la fecha o el alcance.
  function cambiarEstacion(nueva: string) {
    setEstacion(nueva);
    if (pending) {
      revisarArchivo(pending, {
        desde: effectiveFrom,
        alcance,
        hoja,
        estacion: nueva,
      });
    }
  }

  return (
    <div className="upload-panel">
      <h2>Cargar itinerario</h2>
      <p className="upload-hint">
        Acepta tanto el itinerario completo de temporada (una fila por vuelo por día, en
        código IATA — se convierte a OACI automáticamente) como una carga diaria estilo
        hoja "INFO", o un CSV plano. <strong>Soltar el archivo no lo aplica:</strong> primero
        se valida y se muestra qué cambiaría, y recién con "Aplicar carga" se reemplaza el
        itinerario.
      </p>

      <label className="upload-estacion">
        Aeródromo
        <select
          value={estacion}
          disabled={busy}
          onChange={(e) => cambiarEstacion(e.target.value)}
        >
          {/* Mientras el catálogo no llegue, la única opción es la
              preseleccionada: un desplegable vacío haría parecer que no hay
              ninguno. */}
          {estaciones.length === 0 && <option value={estacion}>{estacion}</option>}
          {estaciones.map((e) => (
            <option key={e.codigo_oaci} value={e.codigo_oaci}>
              {e.codigo_oaci} — {e.nombre}
            </option>
          ))}
        </select>
        <span className="upload-hint-small">
          De quién es este itinerario. Cada aeródromo se carga y se reemplaza por
          separado: elegir mal acá no mezcla los datos, los guarda bajo el
          aeródromo equivocado.
        </span>
      </label>

      <label className="upload-effective-from">
        Vigente desde
        <input
          type="date"
          value={effectiveFrom}
          onChange={(e) => cambiarFecha(e.target.value)}
        />
      </label>
      <fieldset className="upload-alcance">
        <legend>Qué reemplaza</legend>
        <label>
          <input
            type="radio"
            name="alcance"
            checked={alcance === "desde"}
            onChange={() => cambiarAlcance("desde")}
          />
          <span>
            <strong>De esa fecha en adelante</strong> — actualización de temporada de la
            DGAC. Borra todo el itinerario posterior y lo reemplaza por el del archivo.
          </span>
        </label>
        <label>
          <input
            type="radio"
            name="alcance"
            checked={alcance === "dia"}
            onChange={() => cambiarAlcance("dia")}
          />
          <span>
            <strong>Solo ese día</strong> — carga diaria suelta. No toca los días
            posteriores ya cargados.
          </span>
        </label>
      </fieldset>

      <p className="upload-hint upload-hint-small">
        Lo anterior a la fecha elegida no se toca nunca, y la carga no modifica ningún
        vuelo de la grilla: cancelar un vuelo se hace desde la grilla.
      </p>

      <div
        className={`upload-dropzone ${dragOver ? "upload-dropzone-active" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) revisarArchivo(file, { desde: effectiveFrom, alcance, estacion });
        }}
        onClick={() => inputRef.current?.click()}
      >
        {busy
          ? "Procesando... (puede tardar unos segundos si es el itinerario completo)"
          : "Arrastra el archivo aquí o haz clic para elegirlo"}
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xls,.csv"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) revisarArchivo(file, { desde: effectiveFrom, alcance, estacion });
          }}
        />
      </div>

      {error && <p className="upload-error">{error}</p>}

      {preview && (
        <div className="upload-preview">
          <h3>
            Revisá antes de aplicar
            {pending && <span className="upload-preview-file"> · {pending.name}</span>}
          </h3>

          <p className="upload-preview-formato">
            Aeródromo: <strong>{preview.estacion}</strong>
            {" — "}
            {estaciones.find((e) => e.codigo_oaci === preview.estacion)?.nombre ?? ""}
            <br />
            Leído como: <strong>{FORMATO_LABEL[preview.formato]}</strong>
            <br />
            Reemplaza:{" "}
            <strong>
              {preview.alcance === "dia"
                ? `solo el ${effectiveFrom}`
                : `del ${effectiveFrom} en adelante`}
            </strong>
          </p>

          {/* El archivo de la DGAC es un libro con varias hojas parciales (la
              de la temporada anterior, la cruda sin convertir, la que arma el
              FMP). Se muestra cuál se leyó y se puede cambiar. */}
          {preview.hojas.length > 1 && (
            <p className="upload-preview-hoja">
              <label>
                Hoja del archivo:{" "}
                <select
                  value={hoja ?? ""}
                  disabled={busy}
                  onChange={(e) => cambiarHoja(e.target.value)}
                >
                  <option value="">
                    Automática{preview.hoja ? ` (${preview.hoja})` : ""}
                  </option>
                  {preview.hojas.map((h) => (
                    <option key={h.nombre} value={h.nombre}>
                      {h.nombre} — {h.filas} filas
                      {h.fecha_min && h.fecha_max ? ` (${h.fecha_min} a ${h.fecha_max})` : ""}
                    </option>
                  ))}
                </select>
              </label>
            </p>
          )}

          <p>
            <strong>{preview.filas_aceptadas}</strong> filas válidas ·{" "}
            <strong>{preview.filas_rechazadas}</strong> rechazadas
            {preview.fecha_min && preview.fecha_max && (
              <>
                {" "}· quedaría cargado del <strong>{preview.fecha_min}</strong> al{" "}
                <strong>{preview.fecha_max}</strong>
              </>
            )}
          </p>

          {preview.advertencias.length > 0 && (
            <ul className="upload-preview-avisos">
              {preview.advertencias.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          )}

          {preview.por_fecha.length > 0 && (
            <table className="upload-preview-table">
              <thead>
                <tr><th>Fecha</th><th>Arribos</th><th>Salidas</th></tr>
              </thead>
              <tbody>
                {preview.por_fecha.slice(0, 10).map((d) => (
                  <tr key={d.fecha}>
                    <td>{d.fecha}</td>
                    <td>{d.arribos}</td>
                    <td>{d.salidas}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {preview.por_fecha.length > 10 && (
            <p className="upload-hint upload-hint-small">
              …y {preview.por_fecha.length - 10} día(s) más.
            </p>
          )}

          {preview.vuelos_sin_itinerario.length > 0 && (
            <p className="upload-hint upload-hint-small">
              Quedan sin itinerario (la carga no los modifica):{" "}
              {preview.vuelos_sin_itinerario.join(", ")}
            </p>
          )}

          {preview.errores.length > 0 && (
            <table className="upload-errors-table">
              <thead><tr><th>Fila</th><th>Motivo</th></tr></thead>
              <tbody>
                {preview.errores.map((e, i) => (
                  <tr key={i}><td>{e.row}</td><td>{e.motivo}</td></tr>
                ))}
              </tbody>
            </table>
          )}

          <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
            <button
              type="button"
              className="upload-process-btn"
              onClick={aplicar}
              disabled={busy || preview.filas_aceptadas === 0}
            >
              {busy
                ? "Aplicando…"
                : alcance === "dia"
                  ? `Aplicar solo el ${effectiveFrom}`
                  : `Aplicar desde el ${effectiveFrom} en adelante`}
            </button>
            <button
              type="button"
              className="upload-discard-btn"
              onClick={descartar}
              disabled={busy}
            >
              Descartar
            </button>
          </div>
          {preview.filas_aceptadas === 0 && (
            <p className="upload-hint upload-hint-small">
              No hay filas válidas para aplicar: revisá el archivo o la fecha elegida.
            </p>
          )}
        </div>
      )}

      {report && (
        <div className="upload-report">
          <p>
            <strong>{report.filas_aceptadas}</strong> filas aceptadas ·{" "}
            <strong>{report.filas_rechazadas}</strong> filas rechazadas
          </p>
          {report.errores.length > 0 && (
            <table className="upload-errors-table">
              <thead><tr><th>Fila</th><th>Motivo</th></tr></thead>
              <tbody>
                {report.errores.map((e, i) => (
                  <tr key={i}><td>{e.row}</td><td>{e.motivo}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <section className="upload-history">
        <h3>Actualizaciones recibidas</h3>
        {uploads.length === 0 && (
          <p className="upload-hint upload-hint-small">Todavía no se cargó ninguna actualización.</p>
        )}
        {uploads.length > 0 && (
          <table className="upload-history-table">
            <thead>
              <tr>
                <th>Cargado (UTC)</th>
                <th>Aeródromo</th>
                <th>Vigente desde</th>
                <th>Alcance</th>
                <th>Archivo</th>
                <th>Filas</th>
                <th>Cargó</th>
              </tr>
            </thead>
            <tbody>
              {uploads.map((u, i) => (
                <tr key={u.id} className={i === 0 ? "latest" : ""}>
                  <td>{fechaHoraUtc(u.uploaded_at)}</td>
                  <td>{u.estacion}</td>
                  <td>
                    {u.effective_from}
                    {i === 0 && <span className="upload-latest-badge">VIGENTE</span>}
                  </td>
                  <td title={u.alcance === "dia" ? "Reemplazó solo ese día" : "Reemplazó de esa fecha en adelante"}>
                    {u.alcance === "dia" ? "solo el día" : "en adelante"}
                  </td>
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
