#!/usr/bin/env bash
#
# Restauración de un respaldo del CTOT.
#
#   ops/restaurar.sh respaldos/ctot_20260904_030000.dump           # ensayo
#   ops/restaurar.sh respaldos/ctot_20260904_030000.dump --sobre-produccion
#
# Por defecto restaura sobre una base de ENSAYO (ctot_ensayo_restauracion), no
# sobre la operativa. Ese es el modo que hay que correr periódicamente: un
# respaldo que nunca se restauró no es un respaldo, es un archivo. Probarlo
# contra una base aparte no interrumpe la operación y responde la única
# pregunta que importa -- ¿esto se puede recuperar, y en cuánto tiempo?
#
# Con --sobre-produccion se restaura sobre la base real. Eso BORRA todo lo que
# haya ahora. Pide confirmación escrita y solo debería usarse en una
# recuperación de verdad.

set -euo pipefail

# En Git Bash (Windows) las rutas absolutas se traducen a rutas de Windows
# antes de llegar al contenedor, y pg_restore recibiría una ruta del host que
# adentro no existe. En Linux esta variable no tiene efecto.
export MSYS_NO_PATHCONV=1

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

VOLCADO="${1:-}"
MODO="${2:-ensayo}"

if [ -z "$VOLCADO" ] || [ ! -f "$VOLCADO" ]; then
  echo "Uso: ops/restaurar.sh <archivo.dump> [--sobre-produccion]" >&2
  echo >&2
  echo "Respaldos disponibles:" >&2
  ls -1t respaldos/ctot_*.dump 2>/dev/null | head -10 >&2 || echo "  (ninguno)" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${POSTGRES_USER:?falta POSTGRES_USER en .env}"
: "${POSTGRES_DB:?falta POSTGRES_DB en .env}"

if [ "$MODO" = "--sobre-produccion" ]; then
  BASE="$POSTGRES_DB"
  echo "!!  Vas a restaurar SOBRE LA BASE OPERATIVA '$BASE'."
  echo "!!  Todo lo que tenga ahora se pierde y se reemplaza por el respaldo."
  echo "!!  Antes de seguir: ¿corriste ops/respaldo.sh para guardar el estado actual?"
  printf '\nEscribí exactamente RESTAURAR para continuar: '
  read -r confirmacion
  if [ "$confirmacion" != "RESTAURAR" ]; then
    echo "Cancelado."
    exit 1
  fi
else
  BASE="ctot_ensayo_restauracion"
  echo "Modo ensayo: se restaura sobre '$BASE'. La base operativa no se toca."
  docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS $BASE;" > /dev/null 2>&1
  docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres \
    -c "CREATE DATABASE $BASE;" > /dev/null
fi

INICIO="$(date +%s)"
echo "[$(date -u +%FT%TZ)] restaurando $VOLCADO -> $BASE"

# El volcado se copia al contenedor antes de restaurar: pg_restore lee el
# índice del final del archivo y necesita reposicionarse, cosa que no puede
# hacer sobre una tubería. La copia va con nombre RELATIVO porque una ruta
# absoluta del host no sobrevive a MSYS_NO_PATHCONV.
TMP_LOCAL="./ctot_restaurar_$$.dump"
TMP_EN_CONTENEDOR="/tmp/ctot_restaurar_$$.dump"
cp "$VOLCADO" "$TMP_LOCAL"
docker compose cp "$TMP_LOCAL" "db:$TMP_EN_CONTENEDOR" > /dev/null
rm -f "$TMP_LOCAL"

# --clean --if-exists: en modo producción borra los objetos antes de
# recrearlos; en una base recién creada no hace nada.
# --no-owner: el volcado puede venir de otra instalación con otro dueño.
# El codigo de salida se captura ANTES de filtrar la salida. Antes esto
# terminaba en `| grep ... || true`, puesto para que grep no fallara cuando
# no quedaba nada que mostrar. Pero eso se tragaba tambien el fallo de
# pg_restore: con un volcado corrupto el script anunciaba 'restaurado' y
# devolvia 0, asi que un cron lo habria dado por bueno. Un respaldo que no
# puede avisar cuando la restauracion falla no sirve para nada.
set +e
SALIDA="$(docker compose exec -T db pg_restore \
  -U "$POSTGRES_USER" -d "$BASE" --clean --if-exists --no-owner "$TMP_EN_CONTENEDOR" 2>&1)"
CODIGO=$?
set -e

docker compose exec -T db rm -f "$TMP_EN_CONTENEDOR"

# Ruido de pg_restore que no le aporta nada a quien restaura.
echo "$SALIDA" | grep -viE "^pg_restore: (connecting|creating|processing|implied)" || true

if [ "$CODIGO" -ne 0 ]; then
  echo >&2
  echo "ERROR: la restauracion FALLO (pg_restore devolvio $CODIGO)." >&2
  echo "La base $BASE quedo incompleta. NO la des por buena." >&2
  exit 1
fi

SEGUNDOS=$(( $(date +%s) - INICIO ))

echo
echo "[$(date -u +%FT%TZ)] restaurado en ${SEGUNDOS}s. Contenido recuperado:"
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$BASE" -t -c "
  SELECT '  vuelos:      ' || count(*) FROM flights
  UNION ALL SELECT '  itinerario:  ' || count(*) FROM itinerary_entries
  UNION ALL SELECT '  turnos:      ' || count(*) FROM shift_logs
  UNION ALL SELECT '  operadores:  ' || count(*) FROM controllers;"

if [ "$MODO" != "--sobre-produccion" ]; then
  echo
  echo "Ensayo terminado. Para eliminar la base de prueba:"
  echo "  docker compose exec -T db psql -U $POSTGRES_USER -d postgres -c 'DROP DATABASE $BASE;'"
fi
