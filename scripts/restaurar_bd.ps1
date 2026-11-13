<#
.SYNOPSIS
  Restaura un respaldo de CALLFLOW en una base NUEVA (por defecto 'callflow_restaurada'),
  para no sobrescribir la base en uso. Revise los datos y luego decida si reemplazar.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\restaurar_bd.ps1 -Archivo respaldos\callflow_20261105_120000.dump
#>
param(
    [Parameter(Mandatory = $true)][string]$Archivo,
    [string]$BaseDestino = "callflow_restaurada",
    [string]$PgBin = ""          # vacío: usa la versión de PostgreSQL más nueva instalada
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
if (-not $PgBin) {
    $PgBin = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Name -replace '\D', '') } -Descending |
        ForEach-Object { Join-Path $_.FullName "bin" } |
        Where-Object { Test-Path (Join-Path $_ "pg_restore.exe") } | Select-Object -First 1
    if (-not $PgBin) { throw "No se encontró PostgreSQL en C:\Program Files\PostgreSQL. Indique -PgBin." }
}
if (-not (Test-Path $Archivo)) { throw "No existe el archivo $Archivo" }

$cfg = @{}
foreach ($l in Get-Content (Join-Path $raiz ".env") -Encoding UTF8) {
    if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { $cfg[$matches[1]] = $matches[2] }
}
if (-not $cfg["DB_ADMIN_USER"]) { throw "Se necesita DB_ADMIN_USER en el .env (lo crea scripts\crear_bd.ps1)" }

try {
    $env:PGPASSWORD = $cfg["DB_ADMIN_PASSWORD"]
    $comun = @("-h", $cfg["DB_HOST"], "-p", $cfg["DB_PORT"], "-U", $cfg["DB_ADMIN_USER"])
    & (Join-Path $PgBin "createdb.exe") @comun -T template0 -E UTF8 $BaseDestino
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear la base $BaseDestino (¿ya existe?)" }
    # Preparar la base igual que scripts\crear_bd.sql: extensión y esquema del usuario de la aplicación
    $app = $cfg["DB_USER"]
    $prep = "CREATE EXTENSION IF NOT EXISTS btree_gist; REVOKE CREATE ON SCHEMA public FROM PUBLIC; " +
            "ALTER SCHEMA public OWNER TO `"$app`"; GRANT USAGE, CREATE ON SCHEMA public TO `"$app`"; " +
            "REVOKE ALL ON DATABASE `"$BaseDestino`" FROM PUBLIC; GRANT CONNECT, TEMPORARY ON DATABASE `"$BaseDestino`" TO `"$app`";"
    & (Join-Path $PgBin "psql.exe") @comun -d $BaseDestino -X -q -v ON_ERROR_STOP=1 -c $prep
    if ($LASTEXITCODE -ne 0) { throw "No se pudo preparar la base $BaseDestino" }
    & (Join-Path $PgBin "pg_restore.exe") @comun -d $BaseDestino --no-owner --no-comments --role $cfg["DB_USER"] --exit-on-error $Archivo
    if ($LASTEXITCODE -ne 0) { throw "pg_restore terminó con error ($LASTEXITCODE)" }
}
finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }

Write-Host "Respaldo restaurado en la base '$BaseDestino'."
