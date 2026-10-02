import { useMemo, useState } from "react";
import { useAppContext } from "../../contexts/AppContext";
import corpacLogo from "../../assets/corpac-logo.png";
import type { Position, Sector } from "../../types";
import { MoonIcon, ShieldIcon, SunIcon } from "../shared/Icons";
import { UtcClock } from "../shared/UtcClock";

interface Props {
  onLogin: (params: { operatorName: string; position: Position; sector: Sector; pin: string }) => void;
  loading: boolean;
  error: string | null;
}

const POSITIONS: Position[] = ["FMP SUR", "FMP NOR", "FMP CUSCO"];

// La pestaña solo orienta a quien entra: el alcance real (solo lectura,
// descarga, año en curso) lo impone el backend según el perfil, así que un
// usuario DGAC queda en solo lectura entre por la pestaña que entre.
type LoginMode = "operador" | "observador";

const RADAR_BLIP_COUNT = 10;

interface RadarBlip {
  top: number;
  left: number;
  duration: number;
  delay: number;
}

/** Puntos del radar decorativo del login: posición (en coordenadas polares
 * para que caigan dentro del círculo, no en cualquier lugar del cuadrado) y
 * ritmo de parpadeo generados al azar en cada carga de la pantalla. */
function useRadarBlips(count: number): RadarBlip[] {
  return useMemo(() => {
    return Array.from({ length: count }, () => {
      const angle = Math.random() * Math.PI * 2;
      const radius = 5 + Math.random() * 38;
      return {
        top: 50 + Math.sin(angle) * radius,
        left: 50 + Math.cos(angle) * radius,
        duration: 2.6 + Math.random() * 3.4,
        delay: Math.random() * 5,
      };
    });
  }, [count]);
}

// El campo aceptaba 4 dígitos fijos, pero las credenciales iniciales que pide
// el despliegue (INITIAL_OPERATOR_PIN y compañía) exigen 6 o más: con el tope
// en 4 eran literalmente imposibles de tipear y nadie podía entrar a una base
// nueva. El mínimo sigue en 4 para no dejar afuera a los perfiles antiguos
// hasta que TI termine de rotar las credenciales.
const PIN_MIN = 4;
const PIN_MAX = 8;

function sectorFromPosition(position: Position): Sector {
  return position === "FMP NOR" ? "NOR" : "SUR";
}

export function Login({ onLogin, loading, error }: Props) {
  const { theme, toggleTheme } = useAppContext();
  const [mode, setMode] = useState<LoginMode>("operador");
  // Aviso de campos incompletos, distinto del `error` que llega del backend.
  const [formError, setFormError] = useState<string | null>(null);
  const [operatorName, setOperatorName] = useState("");
  const [position, setPosition] = useState<Position>("FMP SUR");
  const [pin, setPin] = useState("");
  const radarBlips = useRadarBlips(RADAR_BLIP_COUNT);

  function switchMode(next: LoginMode) {
    if (next === mode) return;
    setMode(next);
    setPin("");
    setOperatorName("");
    setFormError(null);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Antes esto devolvía en silencio: quien tocaba "Iniciar turno" sin
    // completar algo no recibía ninguna señal y parecía que el botón estaba
    // roto. Ahora se dice exactamente qué falta.
    const faltaNombre = !operatorName.trim();
    const faltaPin = !pin.trim();
    if (faltaNombre || faltaPin) {
      setFormError(
        faltaNombre && faltaPin
          ? "Escribí tu usuario y tu PIN para iniciar turno"
          : faltaNombre
            ? "Escribí tu usuario"
            : "Escribí tu PIN para iniciar turno",
      );
      return;
    }
    // El servidor rechaza menos de 4 dígitos; avisarlo acá evita el viaje y
    // el mensaje genérico de "Nombre o PIN incorrecto".
    if (pin.trim().length < PIN_MIN) {
      setFormError(`El PIN tiene al menos ${PIN_MIN} dígitos`);
      return;
    }
    setFormError(null);
    onLogin({ operatorName: operatorName.trim(), position, sector: sectorFromPosition(position), pin: pin.trim() });
  }

  return (
    <div className="login-screen">
      <button
        className="theme-toggle theme-toggle-floating"
        onClick={toggleTheme}
        title={theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
        aria-label={theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
      >
        {theme === "dark" ? <SunIcon /> : <MoonIcon />}
      </button>
      <div className="login-backdrop" aria-hidden="true">
        <div className="radar-stage">
          <svg viewBox="0 0 200 200" className="radar-rings">
            <circle cx="100" cy="100" r="40" />
            <circle cx="100" cy="100" r="70" />
            <circle cx="100" cy="100" r="99" />
            <line x1="100" y1="1" x2="100" y2="199" />
            <line x1="1" y1="100" x2="199" y2="100" />
          </svg>
          <div className="radar-sweep" />
          {radarBlips.map((b, i) => (
            <div
              key={i}
              className="radar-blip"
              style={{
                top: `${b.top}%`,
                left: `${b.left}%`,
                animationDuration: `${b.duration}s`,
                animationDelay: `${b.delay}s`,
              }}
            />
          ))}
        </div>
      </div>

      <div className="login-content">
        <UtcClock size="lg" />

        <form className="login-card" onSubmit={handleSubmit}>
          <div className="login-heading">
            <span className="login-logo-plate">
              <img src={corpacLogo} alt="CORPAC" className="login-logo" />
            </span>
            <span className="login-eyebrow">Gestión de afluencia</span>
            <h1>FMP LIMA - GDP</h1>
            <p className="login-subtitle">Iniciá tu turno para entrar a la posición.</p>
          </div>

          <div className="login-mode-toggle" role="tablist" aria-label="Tipo de acceso">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "operador"}
              className={`login-mode-btn${mode === "operador" ? " active" : ""}`}
              onClick={() => switchMode("operador")}
            >
              Operador FMP
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "observador"}
              className={`login-mode-btn${mode === "observador" ? " active" : ""}`}
              onClick={() => switchMode("observador")}
            >
              <ShieldIcon /> Observador
            </button>
          </div>

          {mode === "operador" ? (
            <>
              <label>
                Usuario
                <input
                  type="text"
                  autoComplete="username"
                  autoCapitalize="characters"
                  spellCheck={false}
                  placeholder="Nombre de usuario"
                  value={operatorName}
                  onChange={(e) => {
                    setOperatorName(e.target.value);
                    setFormError(null);
                  }}
                  autoFocus
                />
              </label>

              <label>
                Posición
                <select value={position} onChange={(e) => setPosition(e.target.value as Position)}>
                  {POSITIONS.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </label>
            </>
          ) : (
            <>
              <label>
                Usuario
                <input
                  type="text"
                  autoComplete="username"
                  autoCapitalize="characters"
                  spellCheck={false}
                  placeholder="Nombre de usuario"
                  value={operatorName}
                  onChange={(e) => {
                    setOperatorName(e.target.value);
                    setFormError(null);
                  }}
                  autoFocus
                />
              </label>

              <div className="login-observer-note">
                <ShieldIcon />
                <div>
                  <strong>Perfil de solo lectura</strong>
                  <span>
                    Ve ambos FMP (SUR y NOR) sin poder modificar datos.
                  </span>
                </div>
              </div>
            </>
          )}

          <label>
            PIN
            <input
              type="password"
              inputMode="numeric"
              autoComplete="off"
              maxLength={PIN_MAX}
              placeholder={"•".repeat(PIN_MIN + 2)}
              value={pin}
              onChange={(e) => {
                setPin(e.target.value.replace(/\D/g, ""));
                setFormError(null);
              }}
            />
          </label>

          {(formError || error) && <p className="login-error">{formError ?? error}</p>}

          <button type="submit" disabled={loading}>
            {loading ? "Ingresando…" : "Iniciar turno"}
          </button>
        </form>

        <p className="login-footnote">Toda la operación se registra en hora UTC</p>
      </div>
    </div>
  );
}
