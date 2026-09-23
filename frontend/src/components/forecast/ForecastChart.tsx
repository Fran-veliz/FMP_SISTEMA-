import { useEffect, useState } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, forecastEnCache } from "../../services/api";
import type { ForecastBucket } from "../../types";

interface Props {
  flightDate: string;
}

// Los tonos salen de la paleta categórica y van por variable CSS, no por hex,
// porque el modo oscuro usa un paso distinto de cada uno -- no el mismo color
// invertido.
//
// El orden de las barras no es casual: define qué colores quedan pegados, y
// esos pares tienen que distinguirse también con daltonismo. Cusco iba en
// amarillo, pero apilado contra el naranja de las otras nacionales ese par no
// llegaba al mínimo (ΔE 13.7 en claro y 4.8 en oscuro, con 15 y 8 de piso).
// En violeta el mismo par pasa holgado.
const COLOR_CUSCO = "var(--series-cusco)"; // violeta
const COLOR_LLEGADAS = "var(--series-llegadas)"; // naranja
const COLOR_TRABAJADO = "var(--series-trabajado)"; // verde
const COLOR_PRONOSTICADO = "var(--series-pronosticado)"; // azul
const COLOR_DESPEGUES = "var(--series-despegues)"; // magenta
const COLOR_OVER_CAPACITY = "var(--status-critical)";

type SerieId = "cusco" | "nacionales" | "trabajado" | "itinerario" | "despegues";

interface Serie {
  id: SerieId;
  label: string;
  color: string;
  /** Ayuda del chip: de dónde sale el número. */
  detalle: string;
}

// Arranca mostrando lo que la FMP gestiona hoy -- arribos nacionales, Cusco y
// lo ya trabajado en la grilla. El total del itinerario y los despegues se
// agregan a pedido: el total aplasta la escala (llega a la capacidad, 49) y
// los despegues todavía no los trabaja el área.
const SERIES: Serie[] = [
  { id: "cusco", label: "Llegadas desde Cusco", color: COLOR_CUSCO, detalle: "Arribos con origen Cusco (SPZO/CUZ)" },
  { id: "nacionales", label: "Otras llegadas nacionales", color: COLOR_LLEGADAS, detalle: "Arribos de origen peruano que no son de Cusco" },
  { id: "trabajado", label: "En gestión (grilla FMP)", color: COLOR_TRABAJADO, detalle: "Vuelos ya cargados en la grilla, sin cancelados" },
  { id: "itinerario", label: "Total del itinerario", color: COLOR_PRONOSTICADO, detalle: "Todas las operaciones del día: arribos + despegues" },
  { id: "despegues", label: "Despegues", color: COLOR_DESPEGUES, detalle: "Despegues del itinerario (aún no gestionados por la FMP)" },
];

const POR_DEFECTO: SerieId[] = ["cusco", "nacionales", "trabajado"];
const SERIES_KEY = "ctot_forecast_series";

function cargarSeleccion(): Set<SerieId> {
  // Preferencia de esta computadora, no del turno: si falla (modo privado,
  // datos borrados) se arranca con las de siempre en vez de romper.
  try {
    const guardado = localStorage.getItem(SERIES_KEY);
    if (guardado) {
      const ids = JSON.parse(guardado) as SerieId[];
      const validos = ids.filter((id) => SERIES.some((s) => s.id === id));
      if (validos.length > 0) return new Set(validos);
    }
  } catch {
    // sin preferencia guardada
  }
  return new Set(POR_DEFECTO);
}

export function ForecastChart({ flightDate }: Props) {
  // Arranca con la última respuesta que ya trajo el mini de la barra de
  // pestañas, si sigue fresca: así el gráfico aparece dibujado en vez de
  // mostrar los ejes vacíos mientras espera su propia petición.
  const [buckets, setBuckets] = useState<ForecastBucket[]>(
    () => forecastEnCache(flightDate)?.buckets ?? []
  );
  const [visibles, setVisibles] = useState<Set<SerieId>>(cargarSeleccion);

  async function refresh() {
    const data = await api.forecast(flightDate);
    setBuckets(data.buckets);
  }

  useEffect(() => {
    // Al cambiar de fecha, lo que hubiera en pantalla es de otro día: se
    // reemplaza por el cache de la nueva fecha (o se vacía) antes de pedirla.
    setBuckets(forecastEnCache(flightDate)?.buckets ?? []);
    refresh();
    const interval = setInterval(refresh, 15_000);
    return () => clearInterval(interval);
  }, [flightDate]);

  function alternar(id: SerieId) {
    setVisibles((prev) => {
      const next = new Set(prev);
      // Siempre queda al menos una serie: un gráfico vacío no le sirve a nadie
      // y deja al operador sin forma obvia de recuperarlo.
      if (next.has(id)) {
        if (next.size === 1) return prev;
        next.delete(id);
      } else {
        next.add(id);
      }
      try {
        localStorage.setItem(SERIES_KEY, JSON.stringify([...next]));
      } catch {
        // la preferencia no se guarda, pero el gráfico igual cambia
      }
      return next;
    });
  }

  const ve = (id: SerieId) => visibles.has(id);
  const capacidad = buckets[0]?.capacidad_maxima ?? 49;
  const hayExceso = buckets.some((b) => b.pronosticado > b.capacidad_maxima);
  const suma = (campo: (b: ForecastBucket) => number) => buckets.reduce((acc, b) => acc + campo(b), 0);
  const totalPronosticado = suma((b) => b.pronosticado);
  const totalDespegues = suma((b) => b.pronosticado_despegues);
  const totalArribos = totalPronosticado - totalDespegues;
  const totalTrabajado = suma((b) => b.trabajado);
  const totalLlegadas = suma((b) => b.pronosticado_llegadas_nacionales);
  const totalCusco = suma((b) => b.pronosticado_llegadas_cusco);
  // Hallazgo F13: sin esto, el operador calculaba a mano en qué barra estaba parado "ahora".
  const nowLabel = buckets[new Date().getUTCHours()]?.rango_hora;
  // La capacidad es del aeródromo completo (arribos + despegues), así que solo
  // tiene sentido dibujarla cuando esa serie está a la vista. Si no, además
  // estiraría la escala hasta 49 y aplastaría las barras de arribos.
  const muestraCapacidad = ve("itinerario");

  return (
    <div className="viz-root forecast-chart">

      <div className="forecast-header">
        <h2>Itinerarios pronosticados — Lima (SUR + NOR)</h2>
        <p className="forecast-subtitle">
          Operaciones por franja horaria UTC · capacidad del aeródromo {capacidad}/hora ·
          {" "}El día tiene {totalPronosticado} operaciones ({totalArribos} arribos y {totalDespegues} despegues);
          {" "}{totalLlegadas} llegadas son nacionales, {totalCusco} desde Cusco, y {totalTrabajado} vuelos ya están en la grilla
          {muestraCapacidad && hayExceso && (
            <span className="forecast-warning"> · hay franjas por encima de la capacidad</span>
          )}
        </p>
      </div>

      {/* Los chips hacen de leyenda y de control a la vez: cada uno lleva su
          color y su nombre, así la identidad de la serie nunca depende solo
          del color. */}
      <div className="forecast-series" role="group" aria-label="Series del gráfico">
        <span className="forecast-series-label">Mostrar:</span>
        {SERIES.map((serie) => {
          const activa = ve(serie.id);
          return (
            <button
              key={serie.id}
              type="button"
              className={`forecast-chip${activa ? " active" : ""}`}
              onClick={() => alternar(serie.id)}
              aria-pressed={activa}
              title={serie.detalle}
            >
              <span
                className="forecast-chip-dot"
                style={{ background: activa ? serie.color : "transparent", borderColor: serie.color }}
              />
              {serie.label}
            </button>
          );
        })}
        {muestraCapacidad && (
          <span className="forecast-series-nota">
            <span className="forecast-series-dash" /> capacidad máxima · barra roja = franja excedida
          </span>
        )}
      </div>

      <ResponsiveContainer width="100%" height={420}>
        <ComposedChart
          data={buckets}
          barGap={2}
          barCategoryGap="10%"
          margin={{ top: 20, right: 16, left: 0, bottom: 4 }}
        >
          <CartesianGrid vertical={false} stroke="var(--gridline)" />
          {/* Solo la hora, horizontal. Las 24 etiquetas "00:00 - 00:59" a -45
              grados ocupaban 70px de alto y no se podían leer de un vistazo;
              el rango completo sigue apareciendo en el tooltip. */}
          <XAxis
            dataKey="rango_hora"
            tickFormatter={(value: string) => String(value).slice(0, 2)}
            tick={{ fontSize: 12, fill: "var(--text-secondary)" }}
            interval={0}
            height={28}
            tickMargin={8}
            stroke="var(--baseline)"
          />
          <YAxis
            tick={{ fontSize: 12, fill: "var(--muted)" }}
            allowDecimals={false}
            stroke="var(--baseline)"
            width={40}
            label={{
              value: "operaciones / hora",
              angle: -90,
              position: "insideLeft",
              style: { fontSize: 11, fill: "var(--muted)", textAnchor: "middle" },
            }}
          />
          <Tooltip
            cursor={{ fill: "var(--gridline)", fillOpacity: 0.35 }}
            contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--gridline)", fontSize: 12 }}
            labelStyle={{ color: "var(--text-primary)" }}
          />
          {muestraCapacidad && <ReferenceLine y={capacidad} stroke="var(--muted)" strokeDasharray="4 4" />}
          {nowLabel && (
            <ReferenceLine
              x={nowLabel}
              stroke="var(--text-primary)"
              strokeWidth={2}
              label={{ value: "ahora", position: "insideTopLeft", fill: "var(--text-primary)", fontSize: 11, offset: 10 }}
            />
          )}

          {/* Sin animación de entrada (isAnimationActive): son ~700 ms hasta ver
              el gráfico terminado, y encima se repetían en cada refresco de 15 s.
              En una posición operativa 24/7 eso es ruido, no información. */}
          {/* Arribos que la FMP secuencia, en una sola barra apilada: Cusco
              abajo, apoyado en el eje, para poder compararlo entre franjas sin
              que lo mueva lo que tenga encima. */}
          {ve("cusco") && (
            <Bar
              dataKey="pronosticado_llegadas_cusco"
              name="Llegadas desde Cusco"
              stackId="arribos"
              fill={COLOR_CUSCO}
              radius={ve("nacionales") ? undefined : [2, 2, 0, 0]}
              maxBarSize={34}
              isAnimationActive={false}
            />
          )}
          {ve("nacionales") && (
            <Bar
              dataKey="pronosticado_llegadas_nacionales_otras"
              name="Otras llegadas nacionales"
              stackId="arribos"
              fill={COLOR_LLEGADAS}
              radius={[2, 2, 0, 0]}
              maxBarSize={34}
              isAnimationActive={false}
            />
          )}
          {ve("trabajado") && (
            <Bar
              dataKey="trabajado"
              name="En gestión (grilla FMP)"
              fill={COLOR_TRABAJADO}
              radius={[2, 2, 0, 0]}
              maxBarSize={34}
              isAnimationActive={false}
            />
          )}
          {ve("itinerario") && (
            <Bar dataKey="pronosticado" name="Total del itinerario" radius={[2, 2, 0, 0]} maxBarSize={34}
              isAnimationActive={false}>
              {buckets.map((b, i) => (
                <Cell key={i} fill={b.pronosticado > b.capacidad_maxima ? COLOR_OVER_CAPACITY : COLOR_PRONOSTICADO} />
              ))}
            </Bar>
          )}
          {ve("despegues") && (
            <Bar
              dataKey="pronosticado_despegues"
              name="Despegues"
              fill={COLOR_DESPEGUES}
              radius={[2, 2, 0, 0]}
              maxBarSize={34}
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
