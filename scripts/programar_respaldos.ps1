<#
.SYNOPSIS
  Programa los respaldos automáticos de CALLFLOW en el Programador de tareas de Windows.

  Correr UNA sola vez y SOLO en la PC que tiene la base (hoy la de Nicolás, 192.168.100.83; ver
  docs/DESPLIEGUE_LINUX.md, sección 6.6). En las PCs de desarrollo no, para no duplicar respaldos.

  Crea dos tareas:
  - «CALLFLOW - Respaldo por hora»: solo la base, cada hora durante la jornada (por defecto lunes a viernes,
    de 09:00 a 19:00). Si la PC se daña a media tarde, se pierde como mucho una hora de trabajo.
  - «CALLFLOW - Respaldo diario»: base + archivos (CV, contratos, fotos, adjuntos), al terminar la jornada.
  Las dos se ponen al día solas si la PC estaba apagada a la hora programada (se ejecutan al encenderla).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\programar_respaldos.ps1
  powershell -ExecutionPolicy Bypass -File scripts\programar_respaldos.ps1 -CopiarA "E:\Respaldos CALLFLOW" -Sabados
  powershell -ExecutionPolicy Bypass -File scripts\programar_respaldos.ps1 -Quitar
#>
param(
    [string]$Inicio = "09:00",       # primer respaldo por hora
    [int]$Horas = 10,                # cuántas horas cubre (09:00 → 19:00)
    [string]$Diario = "19:30",       # respaldo completo con archivos
    [string]$CopiarA = "",           # segundo destino (USB, otro disco, carpeta de red / OneDrive)
    [switch]$Sabados,                # el call center también trabaja los sábados
    [switch]$Quitar                  # elimina las dos tareas
)

$ErrorActionPreference = "Stop"
$nombres = "CALLFLOW - Respaldo por hora", "CALLFLOW - Respaldo diario"

if ($Quitar) {
    foreach ($n in $nombres) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
    Write-Host "Tareas de respaldo eliminadas."
    return
}

$script = Join-Path $PSScriptRoot "respaldar_bd.ps1"
if (-not (Test-Path $script)) { throw "No se encontró $script" }
$raiz = Split-Path -Parent $PSScriptRoot

$dias = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
if ($Sabados) { $dias += "Saturday" }

function Accion([string]$tipo) {
    $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Tipo $tipo"
    if ($CopiarA) { $argumentos += " -CopiarA `"$CopiarA`"" }
    New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argumentos -WorkingDirectory $raiz
}

# StartWhenAvailable: si la PC estaba apagada a esa hora, corre apenas se encienda.
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

# Por hora: disparador semanal con repetición cada hora durante $Horas horas.
$porHora = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At $Inicio
$porHora.Repetition = (New-ScheduledTaskTrigger -Once -At $Inicio -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Hours $Horas)).Repetition

$diario = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At $Diario

Register-ScheduledTask -TaskName $nombres[0] -Action (Accion "hora") -Trigger $porHora -Settings $ajustes -Force `
    -Description "Respaldo de la base de CALLFLOW cada hora durante la jornada (scripts\respaldar_bd.ps1 -Tipo hora)." | Out-Null
Register-ScheduledTask -TaskName $nombres[1] -Action (Accion "diario") -Trigger $diario -Settings $ajustes -Force `
    -Description "Respaldo completo de CALLFLOW: base + archivos (scripts\respaldar_bd.ps1 -Tipo diario)." | Out-Null

Write-Host "Listo. Tareas programadas ($($dias -join ', ')):"
Write-Host "  - $($nombres[0]): cada hora de $Inicio durante $Horas h (solo la base, se conservan 50)."
Write-Host "  - $($nombres[1]): a las $Diario (base + archivos, se conservan 30)."
if ($CopiarA) { Write-Host "  Copia adicional en: $CopiarA" } else {
    Write-Warning "Sin -CopiarA los respaldos quedan en el mismo disco que la base. Conviene un segundo destino."
}
Write-Host "Probar ya: Start-ScheduledTask -TaskName '$($nombres[0])'  y revisar TI › Servidor."
