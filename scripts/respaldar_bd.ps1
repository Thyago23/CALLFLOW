<#
.SYNOPSIS
  Copia de seguridad de CALLFLOW: la base (formato custom de pg_dump, comprimido) y, con la misma
  fecha y hora, los archivos subidos (MEDIA_ROOT: CVs, contratos, fotos, adjuntos, actas) en un .zip.
  Los dos juntos son lo que se lleva al servidor el día de la mudanza.
  Conserva las últimas N copias. Pensado para programarse en el Programador de tareas de Windows
  en la PC que tiene la base (no en las demás).

  Dos tipos (los programa scripts\programar_respaldos.ps1):
  - diario: base + archivos, callflow_<fecha>.dump y callflow_media_<fecha>.zip. Conserva 30.
  - hora:   solo la base, callflow_hora_<fecha>.dump, durante la jornada. Conserva 50 (unos 5 días laborables).
  Con -CopiarA, además copia lo generado a un segundo lugar (disco USB, otro disco, carpeta de red o de OneDrive),
  para que un daño del disco o de la PC no se lleve también los respaldos.
  Si algo falla, lo anota en logs\errores.jsonl: TI lo ve en TI › Servidor junto con los demás errores.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\respaldar_bd.ps1
  powershell -ExecutionPolicy Bypass -File scripts\respaldar_bd.ps1 -Tipo hora
  powershell -ExecutionPolicy Bypass -File scripts\respaldar_bd.ps1 -CopiarA "E:\Respaldos CALLFLOW"
#>
param(
    [ValidateSet("diario", "hora")]
    [string]$Tipo = "diario",
    [string]$Destino = "",
    [int]$Conservar = 0,          # 0: 30 para «diario», 50 para «hora»
    [string]$PgBin = "",          # vacío: usa la versión de PostgreSQL más nueva instalada
    [string]$CopiarA = "",        # segundo destino opcional (USB, otro disco, carpeta de red)
    [switch]$SinArchivos          # solo la base, sin el .zip de MEDIA_ROOT (el tipo «hora» nunca incluye archivos)
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
if ($Tipo -eq "hora") { $SinArchivos = $true }
if ($Conservar -le 0) { $Conservar = if ($Tipo -eq "hora") { 50 } else { 30 } }
$cfg = @{}

function Anotar-Error([string]$mensaje) {
    # Mismo formato que apps/ti/registro_errores.py, para que aparezca en TI › Servidor.
    try {
        $logs = if ($cfg["LOGS_DIR"]) { $cfg["LOGS_DIR"] } else { Join-Path $raiz "logs" }
        New-Item -ItemType Directory -Force -Path $logs | Out-Null
        $linea = [ordered]@{ fecha = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz"); nivel = "ERROR";
                             origen = "respaldo.$Tipo"; mensaje = "Falló el respaldo automático: $mensaje" } | ConvertTo-Json -Compress
        [System.IO.File]::AppendAllText((Join-Path $logs "errores.jsonl"), $linea + "`n", (New-Object System.Text.UTF8Encoding $false))
    } catch { Write-Warning "No se pudo anotar el error: $_" }
}

try {
if (-not $PgBin) {
    # pg_dump tiene que ser de la misma versión que el servidor o más nueva (la base compartida está en PostgreSQL 18).
    $PgBin = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Name -replace '\D', '') } -Descending |
        ForEach-Object { Join-Path $_.FullName "bin" } |
        Where-Object { Test-Path (Join-Path $_ "pg_dump.exe") } | Select-Object -First 1
    if (-not $PgBin) { throw "No se encontró PostgreSQL en C:\Program Files\PostgreSQL. Indique -PgBin." }
}
if (-not $Destino) { $Destino = Join-Path $raiz "respaldos" }
New-Item -ItemType Directory -Force -Path $Destino | Out-Null

foreach ($l in Get-Content (Join-Path $raiz ".env") -Encoding UTF8) {
    if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { $cfg[$matches[1]] = $matches[2] }
}
$bd = $cfg["DB_NAME"]; $servidor = $cfg["DB_HOST"]; $puerto = $cfg["DB_PORT"]
# El superusuario solo entra desde la PC de la base; desde otra PC se respalda con el usuario de la aplicación.
$local = (-not $servidor) -or ($servidor -in @("localhost", "127.0.0.1", "::1"))
$usuario = $cfg["DB_ADMIN_USER"]; $clave = $cfg["DB_ADMIN_PASSWORD"]
if (-not $usuario -or -not $local) { $usuario = $cfg["DB_USER"]; $clave = $cfg["DB_PASSWORD"] }

$sello = "{0:yyyyMMdd_HHmmss}" -f (Get-Date)
$prefijo = if ($Tipo -eq "hora") { "callflow_hora_" } else { "callflow_" }
$archivo = Join-Path $Destino "$prefijo$sello.dump"
$generados = @($archivo)
try {
    $env:PGPASSWORD = $clave
    & (Join-Path $PgBin "pg_dump.exe") -h $servidor -p $puerto -U $usuario -d $bd -Fc -Z 6 -f $archivo
    if ($LASTEXITCODE -ne 0) {
        Remove-Item $archivo -Force -ErrorAction SilentlyContinue
        throw "pg_dump ($PgBin) terminó con error ($LASTEXITCODE). Si dice 'server version mismatch', " +
              "este PostgreSQL es más viejo que el de la base: respalde desde la PC que tiene la base."
    }
    # Verifica que el respaldo se pueda leer
    & (Join-Path $PgBin "pg_restore.exe") --list $archivo | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "El respaldo $archivo no es legible" }
}
finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
Write-Host "Respaldo de la base: $archivo"

if (-not $SinArchivos) {
    $media = $cfg["MEDIA_ROOT"]
    if (-not $media) { $media = Join-Path $raiz "media" }
    if (Test-Path $media) {
        $zip = Join-Path $Destino "callflow_media_$sello.zip"
        Compress-Archive -Path (Join-Path $media "*") -DestinationPath $zip -CompressionLevel Optimal
        Write-Host "Respaldo de los archivos: $zip"
        $generados += $zip
    } else {
        Write-Warning "No existe la carpeta de archivos $media; solo se respaldó la base."
    }
}

$patrones = if ($Tipo -eq "hora") { @("callflow_hora_*.dump") } else { @("callflow_2*.dump", "callflow_media_*.zip") }

if ($CopiarA) {
    # Si el segundo destino no está (USB desconectado), el respaldo local igual queda; solo se anota el aviso.
    try {
        New-Item -ItemType Directory -Force -Path $CopiarA | Out-Null
        foreach ($g in $generados) { Copy-Item $g -Destination $CopiarA -Force }
        foreach ($patron in $patrones) {
            Get-ChildItem $CopiarA -Filter $patron | Sort-Object LastWriteTime -Descending |
                Select-Object -Skip $Conservar | Remove-Item -Force
        }
        Write-Host "Copia en el segundo destino: $CopiarA"
    } catch {
        Anotar-Error "el respaldo local se hizo, pero no se pudo copiar a '$CopiarA': $($_.Exception.Message)"
        Write-Warning "No se pudo copiar a $CopiarA : $_"
    }
}

foreach ($patron in $patrones) {
    Get-ChildItem $Destino -Filter $patron | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $Conservar | Remove-Item -Force
}
}
catch {
    Anotar-Error $_.Exception.Message
    throw
}
