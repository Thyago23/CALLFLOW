<#
.SYNOPSIS
  Copia de seguridad de la base de CALLFLOW (formato custom de pg_dump, comprimido).
  Conserva las últimas N copias. Pensado para programarse en el Programador de tareas de Windows.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\respaldar_bd.ps1
#>
param(
    [string]$Destino = "",
    [int]$Conservar = 30,
    [string]$PgBin = "C:\Program Files\PostgreSQL\17\bin"
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
if (-not $Destino) { $Destino = Join-Path $raiz "respaldos" }
New-Item -ItemType Directory -Force -Path $Destino | Out-Null

$cfg = @{}
foreach ($l in Get-Content (Join-Path $raiz ".env") -Encoding UTF8) {
    if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { $cfg[$matches[1]] = $matches[2] }
}
$usuario = $cfg["DB_ADMIN_USER"]; $clave = $cfg["DB_ADMIN_PASSWORD"]
if (-not $usuario) { $usuario = $cfg["DB_USER"]; $clave = $cfg["DB_PASSWORD"] }
$bd = $cfg["DB_NAME"]; $servidor = $cfg["DB_HOST"]; $puerto = $cfg["DB_PORT"]

$archivo = Join-Path $Destino ("callflow_{0:yyyyMMdd_HHmmss}.dump" -f (Get-Date))
try {
    $env:PGPASSWORD = $clave
    & (Join-Path $PgBin "pg_dump.exe") -h $servidor -p $puerto -U $usuario -d $bd -Fc -Z 6 -f $archivo
    if ($LASTEXITCODE -ne 0) { throw "pg_dump terminó con error ($LASTEXITCODE)" }
    # Verifica que el respaldo se pueda leer
    & (Join-Path $PgBin "pg_restore.exe") --list $archivo | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "El respaldo $archivo no es legible" }
}
finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }

Get-ChildItem $Destino -Filter "callflow_*.dump" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Conservar | Remove-Item -Force

Write-Host "Respaldo creado: $archivo"
