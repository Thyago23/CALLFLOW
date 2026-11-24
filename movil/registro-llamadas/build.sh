#!/usr/bin/env bash
# Compila el APK de «Registro de llamadas» solo con el SDK de Android (sin Gradle). Versión para Linux (y Git Bash);
# en Windows también está build.ps1. Hacen lo mismo y usan la misma carpeta firma/.
# Uso:  ./build.sh        Resultado: salida/RegistroLlamadas.apk
#
# Necesita: JDK 17 o superior (javac, jar, keytool) y el SDK de Android con la plataforma android-36 y build-tools.
# El SDK se busca en $ANDROID_HOME, o en ~/Android/Sdk (Linux) / %LOCALAPPDATA%\Android\Sdk (Windows).
#
# firma/ (no está en el repositorio, GUÁRDELA):
#   registro-llamadas.jks + clave.txt  llave del APK: para instalar una versión nueva encima hay que firmar con la misma.
#   sello.txt                          llave de los reportes de asesores: todos los teléfonos deben tener un APK con la
#                                      misma, o el supervisor rechaza sus PDF.
#   ntfy-tema.txt                      canal de ntfy de los avisos de pausa: si cambia, los supervisores tienen que
#                                      volver a suscribirse y los asesores necesitan el APK nuevo.
set -euo pipefail
raiz="$(cd "$(dirname "$0")" && pwd)"

if [ -n "${ANDROID_HOME:-}" ]; then sdk="$ANDROID_HOME"
elif [ -n "${LOCALAPPDATA:-}" ]; then sdk="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")/Android/Sdk"
else sdk="$HOME/Android/Sdk"; fi
plataforma="$sdk/platforms/android-36/android.jar"
[ -f "$plataforma" ] || { echo "Falta la plataforma android-36 en $sdk (sdkmanager \"platforms;android-36\")." >&2; exit 1; }
bt="$(ls -d "$sdk"/build-tools/*/ | sort -V | tail -1)"; bt="${bt%/}"
exe=""; [ -f "$bt/aapt2.exe" ] && exe=".exe"
# En Git Bash las herramientas de Windows necesitan rutas de Windows.
ruta() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi; }

tmp="$raiz/build"; salida="$raiz/salida"
rm -rf "$tmp"
mkdir -p "$tmp/res" "$tmp/clases" "$tmp/dex" "$tmp/gen" "$salida"

echo "1/6 Recursos (aapt2)"
"$bt/aapt2$exe" compile --dir "$(ruta "$raiz/res")" -o "$(ruta "$tmp/res.zip")"
# Los assets se agregan en el paso 4 con jar (ver build.ps1: aapt2 en Windows guarda subcarpetas con «\»).
"$bt/aapt2$exe" link -o "$(ruta "$tmp/base.apk")" -I "$(ruta "$plataforma")" --manifest "$(ruta "$raiz/AndroidManifest.xml")" \
    --java "$(ruta "$tmp/gen")" --auto-add-overlay "$(ruta "$tmp/res.zip")"

# Llave de los reportes (Sello.java): se genera LlaveSello.java desde firma/sello.txt, que no está en el repositorio.
sello="$raiz/firma/sello.txt"
if [ ! -f "$sello" ]; then
    mkdir -p "$raiz/firma"
    LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 40 > "$sello"
    echo "   Llave de reportes creada en firma/sello.txt (guárdela junto con la llave de firma)."
fi
valor="$(tr -d '\r\n' < "$sello")"
mkdir -p "$tmp/gen/com/callflow/llamadas"
cat > "$tmp/gen/com/callflow/llamadas/LlaveSello.java" <<EOF
package com.callflow.llamadas;
// Generado por build.sh desde firma/sello.txt. No se sube al repositorio.
final class LlaveSello { static final String VALOR = "$valor"; private LlaveSello() {} }
EOF

# Canal privado de ntfy para los avisos de pausa (Vigilante): los asesores publican ahí y los supervisores se
# suscriben. Quien conozca el nombre puede leer los avisos, por eso es largo y al azar y va en firma/.
tema="$raiz/firma/ntfy-tema.txt"
if [ ! -f "$tema" ]; then
    printf 'callflow-%s' "$(LC_ALL=C tr -dc 'a-z0-9' < /dev/urandom | head -c 24)" > "$tema"
    echo "   Canal de avisos creado en firma/ntfy-tema.txt (guárdelo junto con la llave de firma)."
fi
valorTema="$(tr -d '\r\n' < "$tema")"
cat > "$tmp/gen/com/callflow/llamadas/TemaAvisos.java" <<EOF
package com.callflow.llamadas;
// Generado por build.sh desde firma/ntfy-tema.txt. No se sube al repositorio.
final class TemaAvisos {
    private static final String BASE = "$valorTema";
    /** En el emulador los avisos van a un canal de pruebas aparte, para no llegarles a los supervisores reales. */
    static final String VALOR = BASE.isEmpty() ? "" : BASE + (esEmulador() ? "-prueba" : "");
    private static boolean esEmulador() {
        String h = android.os.Build.HARDWARE, f = android.os.Build.FINGERPRINT, p = android.os.Build.PRODUCT;
        return "ranchu".equals(h) || "goldfish".equals(h) || f.startsWith("generic") || f.contains("emulator")
                || p.startsWith("sdk_") || p.contains("_sdk");
    }
    private TemaAvisos() {}
}
EOF

echo "2/6 Java (javac)"
fuentes=()
while IFS= read -r f; do fuentes+=("$(ruta "$f")"); done < <(find "$raiz/src" "$tmp/gen" -name '*.java')
javac -encoding UTF-8 --release 17 -classpath "$(ruta "$plataforma")" -d "$(ruta "$tmp/clases")" -Xlint:-options "${fuentes[@]}"

echo "3/6 Dalvik (d8)"
clases=()
while IFS= read -r f; do clases+=("$(ruta "$f")"); done < <(find "$tmp/clases" -name '*.class')
java -Xmx1g -cp "$(ruta "$bt/lib/d8.jar")" com.android.tools.r8.D8 --release --min-api 29 \
    --lib "$(ruta "$plataforma")" --output "$(ruta "$tmp/dex")" "${clases[@]}"

echo "4/6 Empaquetar"
cp "$tmp/base.apk" "$tmp/sin-firmar.apk"
(cd "$tmp/dex" && jar uf "$(ruta "$tmp/sin-firmar.apk")" classes.dex)
mkdir -p "$tmp/paquete"
cp -r "$raiz/assets" "$tmp/paquete/assets"
jar uf "$(ruta "$tmp/sin-firmar.apk")" -C "$(ruta "$tmp/paquete")" assets
"$bt/zipalign$exe" -f -p 4 "$(ruta "$tmp/sin-firmar.apk")" "$(ruta "$tmp/alineado.apk")"

echo "5/6 Llave de firma"
llave="$raiz/firma/registro-llamadas.jks"; claveArchivo="$raiz/firma/clave.txt"
if [ ! -f "$llave" ]; then
    mkdir -p "$raiz/firma"
    LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24 > "$claveArchivo"
    c="$(cat "$claveArchivo")"
    keytool -genkeypair -keystore "$(ruta "$llave")" -alias registro-llamadas -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass "$c" -keypass "$c" -dname "CN=CALLFLOW, O=CALLFLOW, C=EC"
    echo "   Llave creada en firma/ (guarde la carpeta en un lugar seguro)."
fi
clave="$(tr -d '\r\n' < "$claveArchivo")"

echo "6/6 Firmar (apksigner)"
apk="$salida/RegistroLlamadas.apk"
java -jar "$(ruta "$bt/lib/apksigner.jar")" sign --ks "$(ruta "$llave")" --ks-pass "pass:$clave" --key-pass "pass:$clave" \
    --out "$(ruta "$apk")" "$(ruta "$tmp/alineado.apk")"
java -jar "$(ruta "$bt/lib/apksigner.jar")" verify "$(ruta "$apk")"
rm -f "$salida"/*.idsig
echo ""
echo "Listo: $apk ($(( $(wc -c < "$apk") / 1024 )) KB)"
