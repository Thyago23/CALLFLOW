package com.callflow.llamadas;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.MediaStore;
import android.provider.Settings;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

/**
 * Pantalla única con un WebView. Según cómo se configuró el teléfono la primera vez (inicio.html):
 *  - asesor: asesor.html lee el historial de llamadas del teléfono y genera el PDF para enviar por WhatsApp.
 *  - supervisor: index.html (registro a mano / por foto, como antes) y reportes.html, que une los PDF de los asesores.
 */
public class MainActivity extends Activity {
    /** Usuario del supervisor: sale en la cabecera y en el PDF del registro a mano. */
    static final String USUARIO = "diego.mendoza";
    /** Dirección interna de las páginas (no sale a internet: la intercepta shouldInterceptRequest). */
    static final String HOST_LECTOR = "appassets.androidplatform.net";
    static final String BASE = "https://" + HOST_LECTOR + "/";
    static final String PAGINA = BASE + "index.html";
    private static final int PEDIR_FOTO = 7, PEDIR_REPORTES = 8, PEDIR_PERMISOS = 9;
    private static final String MIGRADO = "migrado-a-https", DATOS_MIGRADOS = "datos-migrados";
    private static final String ROL = "rol", NOMBRE_ASESOR = "asesor-nombre", CHIPS = "chips-trabajo", PIN = "pin";
    static final String ROL_ASESOR = "asesor", ROL_SUPERVISOR = "supervisor";
    static final String TIPO_EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

    private SharedPreferences preferencias() {
        return getSharedPreferences("app", MODE_PRIVATE);
    }

    private WebView web;
    private ValueCallback<Uri[]> esperaFoto;  // respuesta pendiente del <input type="file"> de la página
    private Uri uriCamara;                    // dónde deja la foto la cámara (null si se eligió de la galería)
    private volatile String importacionPendiente = "";  // resultado de PDF recibidos por «Compartir», para reportes.html
    private volatile JSONObject ultimaConsulta;          // lo que el asesor tiene en pantalla: es lo que va en su PDF

    @Override
    protected void onCreate(Bundle estado) {
        super.onCreate(estado);
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#f5f9fc"));
        // Android 15+ dibuja detrás de las barras del sistema. El WebView no respeta su propio padding, así que
        // va entre dos franjas: arriba la de la barra de estado y abajo la de la barra de navegación o el teclado.
        boolean oscuro = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        LinearLayout contenedor = new LinearLayout(this);
        contenedor.setOrientation(LinearLayout.VERTICAL);
        View arriba = new View(this), abajo = new View(this);
        arriba.setBackgroundColor(Color.parseColor("#0a2a4a"));
        abajo.setBackgroundColor(Color.parseColor(oscuro ? "#0f2a45" : "#ffffff"));
        contenedor.addView(arriba, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0));
        contenedor.addView(web, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        contenedor.addView(abajo, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0));
        setContentView(contenedor);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            contenedor.setOnApplyWindowInsetsListener((v, insets) -> {
                Insets barras = insets.getInsets(WindowInsets.Type.systemBars());
                Insets teclado = insets.getInsets(WindowInsets.Type.ime());
                v.setPadding(barras.left, 0, barras.right, 0);
                arriba.getLayoutParams().height = barras.top;
                abajo.getLayoutParams().height = Math.max(barras.bottom, teclado.bottom);
                arriba.requestLayout();
                abajo.requestLayout();
                return WindowInsets.CONSUMED;
            });
        } else {
            contenedor.setFitsSystemWindows(true);
        }

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setSupportZoom(false);
        s.setTextZoom(100);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView vista, WebResourceRequest pedido) {
                return !HOST_LECTOR.equals(pedido.getUrl().getHost());  // solo se navega entre las páginas de la app
            }

            /** Las páginas (assets/) se sirven desde una dirección https propia, sin salir a internet. */
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView vista, WebResourceRequest pedido) {
                Uri u = pedido.getUrl();
                if (!"https".equals(u.getScheme()) || !HOST_LECTOR.equals(u.getHost())) return null;
                String ruta = u.getPath();
                if (ruta != null && ruta.length() > 1 && !ruta.contains("..")) {
                    try {
                        return new WebResourceResponse(tipoArchivo(ruta), ruta.endsWith(".gz") ? null : "utf-8",
                                getAssets().open(ruta.substring(1)));
                    } catch (IOException ignorada) {
                        // sigue abajo: 404
                    }
                }
                return new WebResourceResponse("text/plain", "utf-8", 404, "No encontrado", null,
                        new ByteArrayInputStream(new byte[0]));
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(android.webkit.ConsoleMessage m) {
                android.util.Log.i("CALLFLOW", m.message() + " (" + m.sourceId() + ":" + m.lineNumber() + ")");
                return true;
            }

            /** <input type="file">: con «capture» abre la cámara; sin él, la galería o los archivos. */
            @Override
            public boolean onShowFileChooser(WebView vista, ValueCallback<Uri[]> respuesta, FileChooserParams opciones) {
                if (esperaFoto != null) esperaFoto.onReceiveValue(null);
                esperaFoto = respuesta;
                Intent pedido;
                if (opciones.isCaptureEnabled()) {
                    uriCamara = ProveedorFotos.nuevaFoto(MainActivity.this);
                    pedido = new Intent(MediaStore.ACTION_IMAGE_CAPTURE)
                            .putExtra(MediaStore.EXTRA_OUTPUT, uriCamara)
                            .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    pedido.setClipData(ClipData.newRawUri("foto", uriCamara));
                } else {
                    uriCamara = null;
                    pedido = Intent.createChooser(new Intent(Intent.ACTION_GET_CONTENT).setType("image/*")
                            .addCategory(Intent.CATEGORY_OPENABLE), "Elegir la captura del registro de llamadas");
                }
                try {
                    startActivityForResult(pedido, PEDIR_FOTO);
                    return true;
                } catch (Exception e) {
                    esperaFoto = null;
                    respuesta.onReceiveValue(null);
                    avisar(uriCamara != null ? "No hay una app de cámara en este teléfono." : "No hay una app para elegir fotos.");
                    return true;
                }
            }
        });
        web.addJavascriptInterface(new Puente(), "Android");
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);

        if (estado != null) {
            web.restoreState(estado);
        } else if (!recibir(getIntent())) {
            web.loadUrl(paginaInicial());
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        recibir(intent);
    }

    /** Rol del teléfono. Las instalaciones de antes de la versión 2.0 eran todas del supervisor. */
    private String rol() {
        String r = preferencias().getString(ROL, "");
        if (r.isEmpty() && preferencias().getBoolean(MIGRADO, false)) {
            r = ROL_SUPERVISOR;
            preferencias().edit().putString(ROL, r).apply();
        }
        return r;
    }

    private String paginaInicial() {
        String r = rol();
        if (r.isEmpty()) return BASE + "inicio.html";
        if (ROL_ASESOR.equals(r)) {
            if (RegistroTelefono.permisoLlamadas(this)) CopiaLlamadas.programar(this);
            Vigilante.actualizar(this);
            // Asesores que ya tenían la app (versión 2.1 o antes): se les pide una vez el permiso de notificaciones.
            if (Build.VERSION.SDK_INT >= 33 && !permisoNotificaciones() && !preferencias().getBoolean("notificaciones-pedidas", false)) {
                preferencias().edit().putBoolean("notificaciones-pedidas", true).apply();
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, PEDIR_PERMISOS);
            }
            return BASE + "asesor.html";
        }
        // Primera vez con la página https: pasar lo guardado por la versión 1.4 (página file://), ver migrar.html.
        return preferencias().getBoolean(MIGRADO, false) ? PAGINA : "file:///android_asset/migrar.html";
    }

    private Set<String> chipsTrabajo() {
        Set<String> s = new HashSet<>();
        try {
            JSONArray a = new JSONArray(preferencias().getString(CHIPS, "[]"));
            for (int i = 0; i < a.length(); i++) s.add(a.getString(i));
        } catch (Exception ignorada) {
            // vacío = todos los chips
        }
        return s;
    }

    /** PDF que llegan con «Compartir» (WhatsApp: mantener presionado el PDF › Compartir › Registro de llamadas). */
    private boolean recibir(Intent intent) {
        if (intent == null) return false;
        String accion = intent.getAction();
        if (!Intent.ACTION_SEND.equals(accion) && !Intent.ACTION_SEND_MULTIPLE.equals(accion)) return false;
        List<Uri> uris = new ArrayList<>();
        ClipData clip = intent.getClipData();
        if (clip != null) for (int i = 0; i < clip.getItemCount(); i++) if (clip.getItemAt(i).getUri() != null) uris.add(clip.getItemAt(i).getUri());
        if (uris.isEmpty()) {
            if (Intent.ACTION_SEND_MULTIPLE.equals(accion)) {
                ArrayList<Uri> lista = extrasUris(intent);
                if (lista != null) uris.addAll(lista);
            } else {
                Uri u = extraUri(intent);
                if (u != null) uris.add(u);
            }
        }
        setIntent(new Intent(this, MainActivity.class));  // no se vuelve a importar al girar o volver
        if (!ROL_SUPERVISOR.equals(rol())) {
            Toast.makeText(this, "Solo el teléfono del supervisor recibe reportes de asesores.", Toast.LENGTH_LONG).show();
            web.loadUrl(paginaInicial());
            return true;
        }
        if (uris.isEmpty()) {
            web.loadUrl(BASE + "reportes.html");
            return true;
        }
        new Thread(() -> {
            importacionPendiente = AlmacenReportes.importar(this, uris).toString();
            runOnUiThread(() -> web.loadUrl(BASE + "reportes.html"));
        }).start();
        return true;
    }

    @SuppressWarnings("deprecation")  // la versión con Class es de Android 13; la app va desde Android 10
    private static ArrayList<Uri> extrasUris(Intent i) {
        return i.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
    }

    @SuppressWarnings("deprecation")
    private static Uri extraUri(Intent i) {
        return i.getParcelableExtra(Intent.EXTRA_STREAM);
    }

    @Override
    @SuppressWarnings("deprecation")
    protected void onActivityResult(int pedido, int resultado, Intent datos) {
        super.onActivityResult(pedido, resultado, datos);
        if (pedido == PEDIR_REPORTES) {
            List<Uri> uris = new ArrayList<>();
            if (resultado == RESULT_OK && datos != null) {
                if (datos.getClipData() != null) {
                    for (int i = 0; i < datos.getClipData().getItemCount(); i++) uris.add(datos.getClipData().getItemAt(i).getUri());
                } else if (datos.getData() != null) {
                    uris.add(datos.getData());
                }
            }
            if (uris.isEmpty()) return;
            new Thread(() -> {
                String r = AlmacenReportes.importar(this, uris).toString();
                runOnUiThread(() -> web.evaluateJavascript("window.reportesImportados && window.reportesImportados(" + r + ")", null));
            }).start();
            return;
        }
        if (pedido != PEDIR_FOTO) return;
        ValueCallback<Uri[]> respuesta = esperaFoto;
        esperaFoto = null;
        if (respuesta == null) return;
        Uri foto = null;
        if (resultado == RESULT_OK) {
            if (uriCamara != null) {
                if (ProveedorFotos.hayFoto(this)) foto = uriCamara;
            } else if (datos != null) {
                foto = datos.getData();
            }
        }
        respuesta.onReceiveValue(foto == null ? null : new Uri[]{foto});
    }

    @Override
    public void onRequestPermissionsResult(int pedido, String[] permisos, int[] resultados) {
        super.onRequestPermissionsResult(pedido, permisos, resultados);
        if (pedido != PEDIR_PERMISOS) return;
        boolean llamadas = RegistroTelefono.permisoLlamadas(this);
        if (llamadas && ROL_ASESOR.equals(rol())) CopiaLlamadas.programar(this);
        Vigilante.actualizar(this);
        web.evaluateJavascript("window.permisosCambiaron && window.permisosCambiaron(" + estadoPermisos() + ")", null);
    }

    /** {llamadas, telefono, bloqueado}: bloqueado = se negó con «No volver a preguntar» (solo desde Ajustes). */
    private String estadoPermisos() {
        boolean llamadas = RegistroTelefono.permisoLlamadas(this), telefono = RegistroTelefono.permisoTelefono(this);
        boolean pedido = preferencias().getBoolean("permisos-pedidos", false);
        boolean bloqueado = pedido && ((!llamadas && !shouldShowRequestPermissionRationale(Manifest.permission.READ_CALL_LOG))
                || (!telefono && !shouldShowRequestPermissionRationale(Manifest.permission.READ_PHONE_STATE)));
        return "{\"llamadas\":" + llamadas + ",\"telefono\":" + telefono + ",\"bloqueado\":" + bloqueado
                + ",\"notificaciones\":" + permisoNotificaciones() + "}";
    }

    private boolean instalada(String paquete) {
        try {
            getPackageManager().getPackageInfo(paquete, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    private boolean permisoNotificaciones() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return false;
        android.app.NotificationManager nm = getSystemService(android.app.NotificationManager.class);
        return nm == null || nm.areNotificationsEnabled();
    }

    private static String tipoArchivo(String ruta) {
        if (ruta.endsWith(".html")) return "text/html";
        if (ruta.endsWith(".js")) return "application/javascript";
        if (ruta.endsWith(".css")) return "text/css";
        if (ruta.endsWith(".png")) return "image/png";
        if (ruta.endsWith(".woff2")) return "font/woff2";
        return "application/octet-stream";
    }

    private void avisar(String texto) {
        web.evaluateJavascript("window.avisar && window.avisar(" + JSONObject.quote(texto) + ", 'error')", null);
    }

    @Override
    protected void onSaveInstanceState(Bundle salida) {
        super.onSaveInstanceState(salida);
        web.saveState(salida);
    }

    /** «Atrás»: si la página lo maneja (cierra una ventana, vuelve a la pantalla anterior) bien; si no, sale. */
    private void atras() {
        web.evaluateJavascript("window.atras ? window.atras() : false", valor -> {
            if (!"true".equals(valor)) finish();
        });
    }

    @Override
    protected void onStart() {
        super.onStart();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && callbackAtras == null) {
            android.window.OnBackInvokedCallback cb = this::atras;
            callbackAtras = cb;
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, cb);
        }
    }

    private Object callbackAtras;  // OnBackInvokedCallback (API 33+); Object para cargar también en Android 10-12

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {  // Android 10 a 12
        atras();
    }

    private static String hash(String s) {
        try {
            byte[] h = MessageDigest.getInstance("SHA-256").digest(("cf-pin|" + s).getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : h) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return s;
        }
    }

    private String version() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (PackageManager.NameNotFoundException e) {
            return "";
        }
    }

    private static String error(Exception e) {
        String m = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
        return "{\"ok\":false,\"error\":" + JSONObject.quote(m) + "}";
    }

    private static String guardado(Archivos.Guardado g) {
        return "{\"ok\":true,\"uri\":" + JSONObject.quote(g.uri.toString()) + ",\"nombre\":" + JSONObject.quote(g.nombre) + "}";
    }

    /** Métodos que las páginas llaman como Android.metodo(...). */
    private class Puente {
        @JavascriptInterface
        public String usuario() {
            return USUARIO;
        }

        // ---------------------------------------------------------------- configuración del teléfono

        @JavascriptInterface
        public String configuracion() {
            try {
                return new JSONObject().put("rol", rol()).put("asesor", preferencias().getString(NOMBRE_ASESOR, ""))
                        .put("chips", new JSONArray(preferencias().getString(CHIPS, "[]")))
                        .put("tienePin", !preferencias().getString(PIN, "").isEmpty())
                        .put("version", version()).put("usuario", USUARIO)
                        .put("dispositivo", Build.MANUFACTURER + " " + Build.MODEL).toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        /** {rol, asesor, chips:[id…], pin} → guarda y abre la pantalla del rol. pin vacío = deja el que estaba. */
        @JavascriptInterface
        public String guardarConfiguracion(String json) {
            try {
                JSONObject o = new JSONObject(json);
                String r = o.optString("rol"), nombre = o.optString("asesor").trim().replaceAll("\\s+", " "), pin = o.optString("pin");
                if (!ROL_ASESOR.equals(r) && !ROL_SUPERVISOR.equals(r)) throw new IllegalArgumentException("Elija quién usará el teléfono.");
                if (ROL_ASESOR.equals(r) && nombre.length() < 2) throw new IllegalArgumentException("Escriba el nombre del asesor.");
                if (!pin.isEmpty() && !pin.matches("\\d{4,6}")) throw new IllegalArgumentException("La clave debe tener de 4 a 6 números.");
                String pinGuardado = pin.isEmpty() ? preferencias().getString(PIN, "") : hash(pin);
                if (ROL_ASESOR.equals(r) && pinGuardado.isEmpty()) throw new IllegalArgumentException("Ponga una clave para que el asesor no cambie la configuración.");
                JSONArray chips = o.optJSONArray("chips");
                preferencias().edit().putString(ROL, r).putString(NOMBRE_ASESOR, ROL_ASESOR.equals(r) ? nombre : "")
                        .putString(CHIPS, chips == null ? "[]" : chips.toString()).putString(PIN, pinGuardado).commit();
                Vigilante.actualizar(MainActivity.this);  // arranca en el asesor, se detiene en el supervisor
                runOnUiThread(() -> web.loadUrl(paginaInicial()));
                return "{\"ok\":true}";
            } catch (Exception e) {
                return error(e);
            }
        }

        @JavascriptInterface
        public boolean verificarPin(String pin) {
            String guardado = preferencias().getString(PIN, "");
            return guardado.isEmpty() || guardado.equals(hash(pin));
        }

        /** Abre la configuración (la página pide la clave antes de llamar). */
        @JavascriptInterface
        public void configurar() {
            runOnUiThread(() -> web.loadUrl(BASE + "inicio.html"));
        }

        // ---------------------------------------------------------------- permisos y chips

        @JavascriptInterface
        public String permisos() {
            return estadoPermisos();
        }

        @JavascriptInterface
        public void pedirPermisos() {
            preferencias().edit().putBoolean("permisos-pedidos", true).apply();
            String[] lista = Build.VERSION.SDK_INT >= 33
                    ? new String[]{Manifest.permission.READ_CALL_LOG, Manifest.permission.READ_PHONE_STATE, Manifest.permission.POST_NOTIFICATIONS}
                    : new String[]{Manifest.permission.READ_CALL_LOG, Manifest.permission.READ_PHONE_STATE};
            runOnUiThread(() -> requestPermissions(lista, PEDIR_PERMISOS));
        }

        // ---------------------------------------------------------------- avisos de pausa (ntfy)

        /** Asesor: activar las notificaciones (pide el permiso o, si ya se negó, abre los ajustes de notificaciones). */
        @JavascriptInterface
        public void pedirNotificaciones() {
            runOnUiThread(() -> {
                if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                        && shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS)) {
                    requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, PEDIR_PERMISOS);
                    return;
                }
                try {
                    startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
                } catch (Exception e) {
                    abrirAjustesApp();
                }
            });
        }

        /** Supervisor: datos para suscribirse a los avisos. {servidor, tema, web, limite, repetir} */
        @JavascriptInterface
        public String avisos() {
            try {
                return new JSONObject().put("servidor", Vigilante.SERVIDOR).put("tema", TemaAvisos.VALOR)
                        .put("web", Vigilante.SERVIDOR + "/" + TemaAvisos.VALOR)
                        .put("limite", Vigilante.LIMITE_MIN).put("repetir", Vigilante.REPETIR_MIN)
                        .put("ntfyInstalado", instalada("io.heckel.ntfy")).toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        /** Supervisor: abre ntfy suscrito al canal de avisos; si no está instalada, abre la tienda para instalarla. */
        @JavascriptInterface
        public void abrirNtfy() {
            runOnUiThread(() -> {
                String host = Uri.parse(Vigilante.SERVIDOR).getHost();
                Intent[] intentos = {
                        new Intent(Intent.ACTION_VIEW, Uri.parse("ntfy://" + host + "/" + TemaAvisos.VALOR)),
                        new Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=io.heckel.ntfy")),
                        new Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=io.heckel.ntfy"))};
                for (Intent i : intentos) {
                    try {
                        startActivity(i);
                        return;
                    } catch (Exception ignorada) {
                        // probar el siguiente
                    }
                }
                avisar("No se pudo abrir ntfy ni la tienda de apps.");
            });
        }

        /** Supervisor: manda un aviso de prueba al canal. Responde en window.avisoProbado(ok). */
        @JavascriptInterface
        public void probarAviso() {
            new Thread(() -> {
                boolean ok;
                try {
                    String hora = new SimpleDateFormat("HH:mm", Locale.US).format(new Date());
                    ok = Vigilante.publicar(new JSONObject().put("topic", TemaAvisos.VALOR)
                            .put("title", "Aviso de prueba")
                            .put("message", "Si ve esto, los avisos de pausa de los asesores le van a llegar aquí (" + hora + ").")
                            .put("tags", new JSONArray().put("white_check_mark")));
                } catch (Exception e) {
                    ok = false;
                }
                boolean r = ok;
                runOnUiThread(() -> web.evaluateJavascript("window.avisoProbado && window.avisoProbado(" + r + ")", null));
            }).start();
        }

        @JavascriptInterface
        public void abrirAjustesApp() {
            runOnUiThread(() -> {
                try {
                    startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", getPackageName(), null)));
                } catch (Exception ignorada) {
                    avisar("Abra Ajustes › Aplicaciones › Registro de llamadas › Permisos.");
                }
            });
        }

        @JavascriptInterface
        public String chips() {
            JSONArray a = new JSONArray();
            try {
                for (RegistroTelefono.Chip c : RegistroTelefono.chips(MainActivity.this)) a.put(c.json());
            } catch (Exception ignorada) {
                // lista vacía
            }
            return a.toString();
        }

        // ---------------------------------------------------------------- asesor

        /** Llamadas del periodo (ver RegistroTelefono.consultar). Lo que devuelve es lo que irá en el PDF. */
        @JavascriptInterface
        public String consultar(String desde, String hasta, boolean soloHorario) {
            try {
                JSONObject r = RegistroTelefono.consultar(MainActivity.this, desde, hasta, soloHorario, chipsTrabajo());
                ultimaConsulta = r;
                return r.toString();
            } catch (Exception e) {
                return error(e);
            }
        }

        /**
         * PDF del asesor: se dibuja con la descripción que arma la página y lleva dentro, firmadas, las llamadas
         * de la última consulta (las mismas que se ven en pantalla). Devuelve {ok, uri, nombre, codigo}.
         */
        @JavascriptInterface
        public String generarReporte(String documento) {
            try {
                JSONObject consulta = ultimaConsulta;
                if (consulta == null) throw new IllegalStateException("Vuelva a cargar las llamadas.");
                JSONArray llamadas = consulta.getJSONArray("llamadas");
                if (llamadas.length() == 0) throw new IllegalArgumentException("No hay llamadas en ese periodo.");
                String asesor = preferencias().getString(NOMBRE_ASESOR, "");
                long ahora = System.currentTimeMillis();
                JSONObject datos = new JSONObject().put("v", 1).put("tipo", "reporte-asesor").put("id", UUID.randomUUID().toString())
                        .put("asesor", asesor).put("dispositivo", Build.MANUFACTURER + " " + Build.MODEL)
                        .put("androidId", Settings.Secure.getString(getContentResolver(), Settings.Secure.ANDROID_ID))
                        .put("app", version()).put("generado", ahora)
                        .put("desde", consulta.getString("desde")).put("hasta", consulta.getString("hasta"))
                        .put("soloHorario", consulta.getBoolean("soloHorario"))
                        .put("chips", consulta.getJSONArray("chipsTrabajo")).put("borradas", consulta.optInt("borradas"))
                        .put("llamadas", llamadas);
                String json = datos.toString(), firma = Sello.firma(json), codigo = Sello.codigo(firma);
                JSONObject doc = new JSONObject(documento).put("codigo", codigo);
                byte[] pdf = Sello.incrustar(PdfDocumento.crear(MainActivity.this, doc), json, firma);
                String periodo = consulta.getString("desde").equals(consulta.getString("hasta")) ? consulta.getString("desde")
                        : consulta.getString("desde") + " a " + consulta.getString("hasta");
                String nombre = "Llamadas " + asesor + " " + periodo + " (" + new SimpleDateFormat("HHmm", Locale.US).format(new Date(ahora)) + ").pdf";
                Archivos.Guardado g = Archivos.guardar(MainActivity.this, nombre, "application/pdf", pdf);
                return new JSONObject(guardado(g)).put("codigo", codigo).toString();
            } catch (Exception e) {
                return error(e);
            }
        }

        /** Envía el archivo directo a WhatsApp (o WhatsApp Business); si no está, abre «Compartir». */
        @JavascriptInterface
        public void enviarWhatsApp(String uri) {
            runOnUiThread(() -> {
                for (String paquete : new String[]{"com.whatsapp", "com.whatsapp.w4b"}) {
                    Intent i = new Intent(Intent.ACTION_SEND).setType("application/pdf").setPackage(paquete)
                            .putExtra(Intent.EXTRA_STREAM, Uri.parse(uri)).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    try {
                        startActivity(i);
                        return;
                    } catch (Exception ignorada) {
                        // probar el siguiente
                    }
                }
                lanzar(new Intent(Intent.ACTION_SEND).setType("application/pdf").putExtra(Intent.EXTRA_STREAM, Uri.parse(uri)), "Enviar reporte");
            });
        }

        // ---------------------------------------------------------------- supervisor

        @JavascriptInterface
        public void elegirReportes() {
            Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("application/pdf").addCategory(Intent.CATEGORY_OPENABLE)
                    .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
            runOnUiThread(() -> {
                try {
                    startActivityForResult(i, PEDIR_REPORTES);
                } catch (Exception e) {
                    avisar("No hay una app para elegir archivos en este teléfono.");
                }
            });
        }

        /** Resultado de los PDF que llegaron por «Compartir» (una sola vez); "" si no hay. */
        @JavascriptInterface
        public String tomarImportacion() {
            String r = importacionPendiente;
            importacionPendiente = "";
            return r;
        }

        @JavascriptInterface
        public String reportes() {
            return AlmacenReportes.todos(MainActivity.this).toString();
        }

        @JavascriptInterface
        public boolean quitarReporte(String id) {
            try {
                AlmacenReportes.quitar(MainActivity.this, id);
                return true;
            } catch (Exception e) {
                return false;
            }
        }

        /** PDF a partir de la descripción de la página (consolidado). Devuelve {ok, uri, nombre}. */
        @JavascriptInterface
        public String generarPdf(String documento, String nombre) {
            try {
                byte[] pdf = PdfDocumento.crear(MainActivity.this, new JSONObject(documento));
                return guardado(Archivos.guardar(MainActivity.this, nombre, "application/pdf", pdf));
            } catch (Exception e) {
                return error(e);
            }
        }

        /** Libro de Excel (.xlsx) con formato a partir de la descripción de sus hojas (ver ExcelXlsx). Devuelve {ok, uri, nombre}. */
        @JavascriptInterface
        public String guardarExcel(String nombre, String libro) {
            try {
                byte[] xlsx = ExcelXlsx.crear(new JSONObject(libro));
                return guardado(Archivos.guardar(MainActivity.this, nombre, TIPO_EXCEL, xlsx));
            } catch (Exception e) {
                return error(e);
            }
        }

        // ---------------------------------------------------------------- registro a mano (versión anterior)

        @JavascriptInterface
        public void migrar(String json) {
            preferencias().edit().putString(DATOS_MIGRADOS, json).putBoolean(MIGRADO, true).commit();
            runOnUiThread(() -> web.loadUrl(PAGINA));
        }

        @JavascriptInterface
        public String tomarMigracion() {
            String json = preferencias().getString(DATOS_MIGRADOS, "");
            if (!json.isEmpty()) preferencias().edit().remove(DATOS_MIGRADOS).commit();
            return json;
        }

        @JavascriptInterface
        public String guardarPdf(String json) {
            try {
                GeneradorPdf.Resultado r = GeneradorPdf.guardar(MainActivity.this, json, USUARIO);
                return "{\"ok\":true,\"uri\":" + GeneradorPdf.jsonTexto(r.uri.toString())
                        + ",\"nombre\":" + GeneradorPdf.jsonTexto(r.nombre) + "}";
            } catch (Exception e) {
                return error(e);
            }
        }

        // ---------------------------------------------------------------- abrir y compartir

        @JavascriptInterface
        public void abrirPdf(String uri) {
            abrirArchivo(uri, "application/pdf");
        }

        @JavascriptInterface
        public void abrirArchivo(String uri, String tipo) {
            runOnUiThread(() -> lanzar(new Intent(Intent.ACTION_VIEW).setDataAndType(Uri.parse(uri), tipo), "Abrir con"));
        }

        @JavascriptInterface
        public void compartirPdf(String uri) {
            compartir(uri, "application/pdf");
        }

        @JavascriptInterface
        public void compartir(String uri, String tipo) {
            Intent enviar = new Intent(Intent.ACTION_SEND).setType(tipo).putExtra(Intent.EXTRA_STREAM, Uri.parse(uri));
            runOnUiThread(() -> lanzar(enviar, "Compartir"));
        }

        private void lanzar(Intent intent, String titulo) {
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            try {
                startActivity(Intent.createChooser(intent, titulo));
            } catch (Exception e) {
                avisar("No hay una app para abrir este archivo en el teléfono.");
            }
        }
    }
}
