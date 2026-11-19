# Compila el APK de «Registro de llamadas» solo con el SDK de Android (sin Gradle ni descargas).
# Uso:  powershell -ExecutionPolicy Bypass -File build.ps1
# Resultado: salida\RegistroLlamadas.apk
#
# La firma usa firma\registro-llamadas.jks (se crea la primera vez). GUÁRDELA: para instalar una versión
# nueva encima de la anterior hay que firmar con la MISMA llave; si se pierde, hay que desinstalar la app
# (y se pierde lo que no se haya pasado a PDF).
$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { "$env:LOCALAPPDATA\Android\Sdk" }
$plataforma = "$sdk\platforms\android-36\android.jar"
$bt = Get-ChildItem "$sdk\build-tools" -Directory | Sort-Object { [version]($_.Name -replace '[^\d.]', '') } | Select-Object -Last 1
$bt = $bt.FullName
if (-not (Test-Path $plataforma)) { throw "Falta la plataforma android-36 en $sdk (instálela desde el SDK Manager)." }

$tmp = "$raiz\build"
$salida = "$raiz\salida"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$tmp\res", "$tmp\clases", "$tmp\dex", $salida | Out-Null

function Ejecutar($exe, [string[]]$argumentos) {
    & $exe @argumentos
    if ($LASTEXITCODE -ne 0) { throw "Falló: $exe $($argumentos -join ' ')" }
}

Write-Host "1/6 Recursos (aapt2)"
Ejecutar "$bt\aapt2.exe" @("compile", "--dir", "$raiz\res", "-o", "$tmp\res.zip")
# Los assets NO se pasan con -A: en Windows aapt2 guarda las subcarpetas con «\» (assets/ocr\lector.js) y Android no
# los encuentra. Se agregan en el paso 4 con jar, que usa «/».
Ejecutar "$bt\aapt2.exe" @("link", "-o", "$tmp\base.apk", "-I", $plataforma, "--manifest", "$raiz\AndroidManifest.xml",
    "--java", "$tmp\gen", "--auto-add-overlay", "$tmp\res.zip")

# Llave de los reportes de asesores (Sello.java). Va en firma\ (fuera del repositorio): si se pierde o cambia, el
# supervisor deja de aceptar los PDF de los teléfonos que tengan el APK anterior.
$sello = "$raiz\firma\sello.txt"
if (-not (Test-Path $sello)) {
    New-Item -ItemType Directory -Force "$raiz\firma" | Out-Null
    $nueva = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
    Set-Content -Path $sello -Value $nueva -Encoding ascii -NoNewline
    Write-Host "   Llave de reportes creada en firma\sello.txt (guárdela junto con la llave de firma)."
}
$valorSello = (Get-Content $sello -Raw).Trim()
New-Item -ItemType Directory -Force "$tmp\gen\com\callflow\llamadas" | Out-Null
Set-Content -Path "$tmp\gen\com\callflow\llamadas\LlaveSello.java" -Encoding ascii -Value @"
package com.callflow.llamadas;
// Generado por build.ps1 desde firma\sello.txt. No se sube al repositorio.
final class LlaveSello { static final String VALOR = "$valorSello"; private LlaveSello() {} }
"@

Write-Host "2/6 Java (javac)"
$fuentes = @(Get-ChildItem "$raiz\src", "$tmp\gen" -Recurse -Filter *.java | ForEach-Object { $_.FullName })
Ejecutar "javac" (@("-encoding", "UTF-8", "--release", "17", "-classpath", $plataforma, "-d", "$tmp\clases", "-Xlint:-options") + $fuentes)

Write-Host "3/6 Dalvik (d8)"
$clases = @(Get-ChildItem "$tmp\clases" -Recurse -Filter *.class | ForEach-Object { $_.FullName })
# Se llama a Java directamente: d8.bat a veces se queda colgado al ejecutarse desde PowerShell.
Ejecutar "java" (@("-Xmx1g", "-cp", "$bt\lib\d8.jar", "com.android.tools.r8.D8", "--release", "--min-api", "29",
    "--lib", $plataforma, "--output", "$tmp\dex") + $clases)

Write-Host "4/6 Empaquetar"
Copy-Item "$tmp\base.apk" "$tmp\sin-firmar.apk"
Push-Location "$tmp\dex"
try { Ejecutar "jar" @("uf", "$tmp\sin-firmar.apk", "classes.dex") } finally { Pop-Location }
New-Item -ItemType Directory -Force "$tmp\paquete" | Out-Null
Copy-Item "$raiz\assets" "$tmp\paquete\assets" -Recurse
Ejecutar "jar" @("uf", "$tmp\sin-firmar.apk", "-C", "$tmp\paquete", "assets")
Ejecutar "$bt\zipalign.exe" @("-f", "-p", "4", "$tmp\sin-firmar.apk", "$tmp\alineado.apk")

Write-Host "5/6 Llave de firma"
$llave = "$raiz\firma\registro-llamadas.jks"
$claveArchivo = "$raiz\firma\clave.txt"
if (-not (Test-Path $llave)) {
    New-Item -ItemType Directory -Force "$raiz\firma" | Out-Null
    $clave = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
    Set-Content -Path $claveArchivo -Value $clave -Encoding ascii -NoNewline
    Ejecutar "keytool" @("-genkeypair", "-keystore", $llave, "-alias", "registro-llamadas", "-keyalg", "RSA", "-keysize", "2048",
        "-validity", "10000", "-storepass", $clave, "-keypass", $clave, "-dname", "CN=CALLFLOW, O=CALLFLOW, C=EC")
    Write-Host "   Llave creada en firma\ (guarde la carpeta en un lugar seguro)."
}
$clave = (Get-Content $claveArchivo -Raw).Trim()

Write-Host "6/6 Firmar (apksigner)"
$apk = "$salida\RegistroLlamadas.apk"
$firmador = @("-jar", "$bt\lib\apksigner.jar")
Ejecutar "java" ($firmador + @("sign", "--ks", $llave, "--ks-pass", "pass:$clave", "--key-pass", "pass:$clave", "--out", $apk, "$tmp\alineado.apk"))
Ejecutar "java" ($firmador + @("verify", $apk))
Remove-Item "$salida\*.idsig" -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "Listo: $apk ($([math]::Round((Get-Item $apk).Length / 1KB)) KB)"
