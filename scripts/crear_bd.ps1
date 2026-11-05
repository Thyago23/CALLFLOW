<#
.SYNOPSIS
  Crea la base de datos de CALLFLOW, un superusuario propio de la empresa y el usuario de la aplicación.

.DESCRIPTION
  Se conecta con un superusuario EXISTENTE (por defecto 'postgres') solo para crear:
    - callflow_admin : superusuario de la empresa (mantenimiento y respaldos)
    - callflow_app   : usuario que usa Django, sin privilegios de superusuario
    - la base de datos 'callflow'
  Las contraseñas de callflow_admin y callflow_app se generan al azar y se guardan en el .env
  del proyecto. La contraseña del superusuario existente NO se guarda en ningún lado.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\crear_bd.ps1
#>
param(
    [string]$SuperUsuario = "postgres",
    [string]$ClaveSuperUsuario = "",
    [string]$Servidor = "localhost",
    [int]$Puerto = 5432,
    [string]$Psql = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$rutaEnv = Join-Path $raiz ".env"

if (-not (Test-Path $Psql)) { throw "No se encontró psql en '$Psql'. Use -Psql para indicar la ruta." }
if (-not (Test-Path $rutaEnv)) { throw "No existe $rutaEnv. Copie .env.example como .env primero." }

function Leer-Env([string]$ruta) {
    $valores = @{}
    foreach ($linea in Get-Content $ruta -Encoding UTF8) {
        if ($linea -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { $valores[$matches[1]] = $matches[2] }
    }
    return $valores
}

function Nueva-Clave {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x')
}

$envActual = Leer-Env $rutaEnv
$dbNombre = if ($envActual["DB_NAME"]) { $envActual["DB_NAME"] } else { "callflow" }
$appUsuario = if ($envActual["DB_USER"]) { $envActual["DB_USER"] } else { "callflow_app" }
$appClave = $envActual["DB_PASSWORD"]
if (-not $appClave -or $appClave -eq "cambie-esto") { throw "Defina DB_PASSWORD en el .env" }

$adminUsuario = if ($envActual["DB_ADMIN_USER"]) { $envActual["DB_ADMIN_USER"] } else { "callflow_admin" }
$adminClave = $envActual["DB_ADMIN_PASSWORD"]
if (-not $adminClave) {
    $adminClave = Nueva-Clave
    Add-Content -Path $rutaEnv -Encoding UTF8 -Value "`n# Superusuario propio de la empresa (mantenimiento y respaldos)`nDB_ADMIN_USER=$adminUsuario`nDB_ADMIN_PASSWORD=$adminClave"
    Write-Host "Se guardó la contraseña de $adminUsuario en .env"
}

if ($ClaveSuperUsuario) {
    $seguro = ConvertTo-SecureString $ClaveSuperUsuario -AsPlainText -Force
} else {
    $seguro = Read-Host "Contraseña del superusuario existente '$SuperUsuario'" -AsSecureString
}
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:PGCLIENTENCODING = "UTF8"
    & $Psql -h $Servidor -p $Puerto -U $SuperUsuario -d postgres -X -q `
        -v "admin_user=$adminUsuario" -v "admin_password=$adminClave" `
        -v "app_user=$appUsuario" -v "app_password=$appClave" -v "db_name=$dbNombre" `
        -f (Join-Path $PSScriptRoot "crear_bd.sql")
    if ($LASTEXITCODE -ne 0) { throw "psql terminó con error ($LASTEXITCODE)" }
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Listo. Siguiente paso:  python manage.py migrate  y  python manage.py inicializar_datos"
