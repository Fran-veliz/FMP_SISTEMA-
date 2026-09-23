# Respaldo diario de la base de SISTEMA_CTOT (Windows / Programador de tareas).
#
# Usa pg_dump en formato custom (-Fc) dentro del contenedor `db` y copia el
# archivo binario al host con `docker compose cp` -- evita por completo los
# problemas de codificacion de redirigir la salida de pg_dump por PowerShell.
#
# TRES COSAS QUE ANTES FALLABAN Y AHORA NO:
#
# 1. La retencion borraba CUALQUIER ctot_*.dump de mas de 14 dias, incluidos
#    los que alguien saco a mano antes de una operacion riesgosa y bautizo
#    para conservar (ctot_antes_carga_agosto, ctot_pre-cambios-mayores...).
#    Se perdieron cuatro asi. Ahora la rotacion solo alcanza a los AUTOMATICOS,
#    que son los que este script nombra con fecha y hora exactas.
#
# 2. Fallaba en silencio. La tarea figuraba "Ready" en el Programador y nadie
#    se enteraba de que hacia seis semanas que no producia nada. Ahora cada
#    corrida deja linea en backups\respaldo.log y el resultado de la ultima en
#    backups\ULTIMO_ESTADO.txt, que se lee de un vistazo.
#
# 3. No distinguia "Docker apagado" de un error real. Es la causa mas comun en
#    una maquina de escritorio: a las 03:00 Docker Desktop suele estar cerrado.
#    Ahora se detecta primero y se dice con todas las letras.
#
# En el servidor de GTIC este problema desaparece: Docker corre como servicio.
# Ahi conviene usar ops/respaldo.sh, que hace lo mismo en Linux.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$backupDir = Join-Path $root "backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$logFile = Join-Path $backupDir "respaldo.log"
$estadoFile = Join-Path $backupDir "ULTIMO_ESTADO.txt"

function Escribir($nivel, $mensaje) {
    $linea = "{0} {1,-5} {2}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $nivel, $mensaje
    Write-Output $linea
    Add-Content -Path $logFile -Value $linea -Encoding utf8
}

function Terminar($nivel, $mensaje) {
    Escribir $nivel $mensaje
    $resumen = "{0}`r`n{1}`r`n{2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $nivel, $mensaje
    Set-Content -Path $estadoFile -Value $resumen -Encoding utf8
    if ($nivel -eq "ERROR") { exit 1 }
    exit 0
}

# --- Docker vivo? Es la causa numero uno del fallo silencioso.
try {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker info devolvio $LASTEXITCODE" }
} catch {
    Terminar "ERROR" "Docker no esta corriendo. El respaldo NO se hizo. En una maquina de escritorio Docker Desktop suele estar cerrado de madrugada: hay que dejarlo abierto, configurarlo para arrancar con Windows, o mover el respaldo a un horario con la maquina en uso."
}

# --- credenciales
$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) {
    Terminar "ERROR" "No se encontro .env en $root -- hace falta para POSTGRES_USER/POSTGRES_DB."
}
$envVars = @{}
Get-Content $envPath | Where-Object { $_ -match "^[^#=\s][^=]*=.*$" } | ForEach-Object {
    $k, $v = $_ -split "=", 2
    $envVars[$k.Trim()] = $v.Trim()
}
$pgUser = $envVars["POSTGRES_USER"]
$pgDb = $envVars["POSTGRES_DB"]
if (-not $pgUser -or -not $pgDb) {
    Terminar "ERROR" "No se pudo leer POSTGRES_USER/POSTGRES_DB desde .env."
}

$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$containerFile = "/tmp/ctot_backup_$timestamp.dump"
$hostFile = Join-Path $backupDir "ctot_$timestamp.dump"

Escribir "INFO" "volcando $pgDb"

docker compose exec -T db pg_dump -U $pgUser -d $pgDb -Fc -f $containerFile
if ($LASTEXITCODE -ne 0) {
    docker compose exec -T db rm -f $containerFile 2>&1 | Out-Null
    Terminar "ERROR" "pg_dump fallo (codigo $LASTEXITCODE). El respaldo NO se hizo."
}

# --- verificacion: un volcado truncado pesa parecido a uno bueno y no avisa.
$indice = docker compose exec -T db pg_restore --list $containerFile 2>$null
$tablas = ($indice | Select-String "TABLE DATA").Count
if ($tablas -lt 1) {
    docker compose exec -T db rm -f $containerFile 2>&1 | Out-Null
    Terminar "ERROR" "El volcado no se puede leer o no tiene datos. Se descarta."
}

docker compose cp "db:$containerFile" $hostFile
if ($LASTEXITCODE -ne 0) {
    docker compose exec -T db rm -f $containerFile 2>&1 | Out-Null
    Terminar "ERROR" "No se pudo copiar el volcado fuera del contenedor."
}
docker compose exec -T db rm -f $containerFile 2>&1 | Out-Null

$mb = [math]::Round((Get-Item $hostFile).Length / 1MB, 1)

# --- rotacion: SOLO los automaticos.
# El patron exige ctot_ + fecha + _ + hora, que es exactamente como nombra este
# script. Un ctot_antes_carga_agosto_2026-09-02.dump no coincide y no se toca
# nunca: si alguien se tomo el trabajo de nombrarlo, es porque lo quiere.
$patronAutomatico = '^ctot_\d{4}-\d{2}-\d{2}_\d{6}\.dump$'
$viejos = Get-ChildItem $backupDir -Filter "ctot_*.dump" |
    Where-Object { $_.Name -match $patronAutomatico -and $_.LastWriteTime -lt (Get-Date).AddDays(-14) }
if ($viejos) {
    $viejos | Remove-Item -Force
    Escribir "INFO" "rotacion: se borraron $($viejos.Count) respaldo(s) automatico(s) de mas de 14 dias"
}

# --- copia fuera de esta maquina.
#
# Un respaldo en el mismo disco que la base NO protege contra la falla de ese
# disco, ni contra un borrado por error. Mientras esto no este configurado,
# todos los puntos de retorno viven en un unico lugar.
#
# Se configura poniendo RESPALDO_EXTERNO en el .env con la ruta destino: un
# disco externo, una carpeta de red de TI, o una nube (esto ultimo, consultado
# antes con TI: son datos operativos de CORPAC, no archivos personales).
#
# Si la ruta no existe o no se puede escribir, se AVISA pero no se pierde el
# respaldo local: mejor una copia que ninguna.
$copiaExterna = " SIN copia externa: definir RESPALDO_EXTERNO en .env para no dejar todo en este disco."
$destino = $envVars["RESPALDO_EXTERNO"]
if ($destino) {
    try {
        if (-not (Test-Path $destino)) { New-Item -ItemType Directory -Force -Path $destino | Out-Null }
        Copy-Item $hostFile -Destination $destino -Force
        $copiaExterna = " Copiado ademas a $destino."
    } catch {
        Escribir "AVISO" "No se pudo copiar a $destino ($($_.Exception.Message)). El respaldo local SI se hizo."
        $copiaExterna = " ATENCION: la copia externa fallo, revisar el log."
    }
}

$conservados = (Get-ChildItem $backupDir -Filter "ctot_*.dump").Count
Terminar "OK" "Respaldo creado: ctot_$timestamp.dump ($mb MB, $tablas tablas). $conservados archivo(s) en backups\.$copiaExterna"
