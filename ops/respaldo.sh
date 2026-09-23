#!/usr/bin/env bash
#
# Respaldo diario de la base del CTOT.
#
# Corre en el HOST (no dentro de un contenedor) y usa el contenedor de la base
# para volcar. Se programa con cron:
#
#   0 3 * * *  /ruta/a/SISTEMA_CTOT/ops/respaldo.sh >> /var/log/ctot-respaldo.log 2>&1
#
# Por qué el volcado se VERIFICA y no solo se crea: un archivo truncado o
# corrupto pesa parecido a uno bueno y no avisa. Sin abrirlo, uno cree tener
# respaldos hasta el día que los necesita. Acá se lee el índice del volcado
# antes de darlo por válido; si falla, el archivo se descarta y el script
# termina con error para que el cron lo reporte.
#
# Lo que este script NO hace: sacar la copia fuera del servidor. Un respaldo
# en el mismo disco que la base no protege contra la falla de ese disco. Hay
# que sincronizar RESPALDO_DIR a otro equipo o a la unidad de red de TI.

set -euo pipefail

# En Git Bash (Windows), las rutas absolutas de los argumentos se traducen a
# rutas de Windows antes de llegar al contenedor. En Linux no tiene efecto.
export MSYS_NO_PATHCONV=1

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

# Misma carpeta que usa scripts/backup-db.ps1: dos scripts escribiendo en
# lugares distintos dejaban la mitad de las copias en cada lado y nadie sabía
# cuál mirar.
RESPALDO_DIR="${RESPALDO_DIR:-$RAIZ/backups}"
# Cuántos volcados diarios se conservan. Con ~40 MB cada uno, 30 días son
# poco más de 1 GB.
RETENCION_DIAS="${RETENCION_DIAS:-30}"

if [ ! -f .env ]; then
  echo "ERROR: falta .env en $RAIZ. Sin él no se conocen las credenciales." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${POSTGRES_USER:?falta POSTGRES_USER en .env}"
: "${POSTGRES_DB:?falta POSTGRES_DB en .env}"

mkdir -p "$RESPALDO_DIR"
# Mismo formato que scripts/backup-db.ps1 (con guiones en la fecha): la
# rotación de ambos identifica los automáticos por ese patrón exacto, y un
# nombre distinto los volvería invisibles para el otro script.
SELLO="$(date -u +%Y-%m-%d_%H%M%S)"
DESTINO="$RESPALDO_DIR/ctot_${SELLO}.dump"

echo "[$(date -u +%FT%TZ)] volcando $POSTGRES_DB -> $DESTINO"

# El volcado se hace a un archivo DENTRO del contenedor y recién después se
# copia afuera. No se redirige la salida de pg_dump directamente al host
# porque la verificación siguiente necesita un archivo de verdad:
# `pg_restore --list` lee el índice al final del volcado y para eso tiene que
# poder reposicionarse, cosa que no puede hacer sobre una tubería.
TMP_EN_CONTENEDOR="/tmp/ctot_respaldo_${SELLO}.dump"

# -Fc: formato propio de Postgres, comprimido y con restauración selectiva.
# Un .sql plano ocuparía varias veces más y no permite restaurar una sola tabla.
if ! docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$TMP_EN_CONTENEDOR"; then
  echo "ERROR: pg_dump falló." >&2
  docker compose exec -T db rm -f "$TMP_EN_CONTENEDOR" 2>/dev/null || true
  exit 1
fi

# Verificación real: si no se puede leer el índice, el volcado no sirve.
TABLAS="$(docker compose exec -T db pg_restore --list "$TMP_EN_CONTENEDOR" 2>/dev/null | grep -c 'TABLE DATA' || true)"
if [ "${TABLAS:-0}" -lt 1 ]; then
  echo "ERROR: el volcado no se puede leer o no tiene datos. Se descarta." >&2
  docker compose exec -T db rm -f "$TMP_EN_CONTENEDOR" 2>/dev/null || true
  exit 1
fi

# La copia se recibe con nombre RELATIVO y después se mueve con el shell.
# Con MSYS_NO_PATHCONV activo (necesario para la ruta del contenedor), una
# ruta absoluta del host tipo /c/Users/... llegaría sin traducir y docker la
# rechazaría. Una ruta relativa no sufre esa traducción en ningún sistema.
docker compose cp "db:$TMP_EN_CONTENEDOR" "./ctot_tmp_${SELLO}.dump" > /dev/null
mv "./ctot_tmp_${SELLO}.dump" "$DESTINO"
docker compose exec -T db rm -f "$TMP_EN_CONTENEDOR"

TAMANO="$(du -h "$DESTINO" | cut -f1)"
echo "[$(date -u +%FT%TZ)] OK: $TAMANO, $TABLAS tablas con datos"

# Rotación: SOLO los automáticos. El patrón exige ctot_ + fecha + _ + hora,
# que es exactamente como nombran los dos scripts. Un
# ctot_antes_carga_agosto_2026-09-02.dump no coincide y no se toca nunca: si
# alguien se tomó el trabajo de nombrarlo, es porque lo quiere conservar.
#
# La versión anterior borraba cualquier ctot_*.dump viejo y se llevó por
# delante cuatro respaldos, dos de ellos guardados a propósito.
BORRADOS="$(find "$RESPALDO_DIR" -regextype posix-extended   -regex '.*/ctot_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}\.dump'   -mtime "+$RETENCION_DIAS" -print -delete | wc -l)"
if [ "$BORRADOS" -gt 0 ]; then
  echo "[$(date -u +%FT%TZ)] rotación: $BORRADOS volcado(s) de más de $RETENCION_DIAS días"
fi

CONSERVADOS="$(find "$RESPALDO_DIR" -name 'ctot_*.dump' | wc -l)"
echo "[$(date -u +%FT%TZ)] listo. $CONSERVADOS volcado(s) en $RESPALDO_DIR"
echo "RECORDATORIO: sincronizar $RESPALDO_DIR fuera de este servidor."
