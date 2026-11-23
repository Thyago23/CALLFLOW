package com.callflow.llamadas;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.database.ContentObserver;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.provider.CallLog;
import android.telephony.PhoneStateListener;
import android.telephony.TelephonyCallback;
import android.telephony.TelephonyManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Teléfono del asesor: avisa cuando pasan LIMITE_MIN minutos sin llamar (y luego cada REPETIR_MIN más).
 * El aviso sale en el propio teléfono y le llega al supervisor por ntfy (app gratuita de notificaciones).
 *
 * Cuenta solo de lunes a sábado de 9:00 a 18:00, sin la hora de almuerzo (13:00 a 14:00), y no mientras hay
 * una llamada en curso. Cuenta como actividad lo mismo que el reporte: las llamadas que hizo el asesor (contesten
 * o no) y las que contestó, de los chips de trabajo. Al empezar el día cuenta desde las 9:00.
 *
 * No revisa cada minuto: calcula a qué hora se cumplen los minutos y deja una alarma exacta a esa hora. Cada
 * llamada nueva (historial) o cambio de estado del teléfono vuelve a calcular. Es un servicio en primer plano
 * (notificación fija) para que Android no lo cierre y tenga internet aunque la pantalla esté apagada.
 */
public class Vigilante extends Service {
    static final int LIMITE_MIN = 8, REPETIR_MIN = 15;  // LIMITE_MIN igual a C.PAUSA_LARGA de assets/comun.js
    static final String SERVIDOR = "https://ntfy.sh";
    private static final String INICIO = "09:00", FIN = "18:00", ALMUERZO_INI = "13:00", ALMUERZO_FIN = "14:00";
    private static final String CANAL_FIJO = "vigilante", CANAL_AVISOS = "avisos-pausa";
    private static final int NOTI_FIJA = 1, NOTI_AVISO = 2;
    private static final String REVISAR = "com.callflow.llamadas.REVISAR";
    private static final long MIN = 60_000L;

    private HandlerThread hilo;
    private Handler h;
    private ContentObserver observador;
    private Object escucha;  // TelephonyCallback (Android 12+) o PhoneStateListener
    private volatile boolean enLlamada;
    private final Runnable revisar = this::revisar;

    // ------------------------------------------------------------------ arrancar / detener

    /** Arranca o detiene el vigilante según el rol del teléfono y los permisos. Se llama al abrir la app, al guardar
     *  la configuración, al dar permisos, al reiniciar el teléfono y al actualizar la app. */
    static void actualizar(Context ctx) {
        try {
            if (debeVigilar(ctx)) ctx.startForegroundService(new Intent(ctx, Vigilante.class).setAction(REVISAR));
            else {
                ctx.stopService(new Intent(ctx, Vigilante.class));
                ctx.getSystemService(AlarmManager.class).cancel(alarma(ctx));
            }
        } catch (Exception ignorada) {
            // Android no deja arrancarlo ahora (app en segundo plano): se intenta la próxima vez que se abra
        }
    }

    static boolean debeVigilar(Context ctx) {
        return MainActivity.ROL_ASESOR.equals(prefs(ctx).getString("rol", "")) && RegistroTelefono.permisoLlamadas(ctx)
                && !TemaAvisos.VALOR.isEmpty();
    }

    private static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences("app", MODE_PRIVATE);
    }

    private static PendingIntent alarma(Context ctx) {
        return PendingIntent.getForegroundService(ctx, 0, new Intent(ctx, Vigilante.class).setAction(REVISAR),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        crearCanales(this);
        enPrimerPlano();
        hilo = new HandlerThread("vigilante");
        hilo.start();
        h = new Handler(hilo.getLooper());
        observador = new ContentObserver(h) {
            @Override
            public void onChange(boolean propio) {
                pedirRevision(1500);
            }
        };
        try {
            getContentResolver().registerContentObserver(CallLog.Calls.CONTENT_URI, true, observador);
        } catch (Exception ignorada) {
            // sin permiso: debeVigilar() lo detiene
        }
        h.post(this::escucharLlamadas);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        enPrimerPlano();
        if (!debeVigilar(this)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        pedirRevision(0);
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        try {
            getContentResolver().unregisterContentObserver(observador);
        } catch (Exception ignorada) {
            // ya no estaba
        }
        dejarDeEscucharLlamadas();
        if (hilo != null) hilo.quitSafely();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void enPrimerPlano() {
        Notification n = new Notification.Builder(this, CANAL_FIJO)
                .setSmallIcon(android.R.drawable.ic_menu_recent_history)
                .setContentTitle("Registro de llamadas activo")
                .setContentText("Si pasan " + LIMITE_MIN + " min sin llamar, se le avisa a usted y al supervisor.")
                .setContentIntent(abrirApp())
                .setOngoing(true)
                .build();
        try {
            if (Build.VERSION.SDK_INT >= 34) startForeground(NOTI_FIJA, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            else startForeground(NOTI_FIJA, n);
        } catch (Exception ignorada) {
            // Android 12+ puede negarlo si se reinicia solo en segundo plano; vuelve al abrir la app o con la alarma
        }
    }

    private PendingIntent abrirApp() {
        return PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE);
    }

    static void crearCanales(Context ctx) {
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        NotificationChannel fijo = new NotificationChannel(CANAL_FIJO, "Vigilancia de pausas", NotificationManager.IMPORTANCE_MIN);
        fijo.setShowBadge(false);
        NotificationChannel avisos = new NotificationChannel(CANAL_AVISOS, "Avisos de tiempo sin llamar", NotificationManager.IMPORTANCE_HIGH);
        avisos.enableVibration(true);
        nm.createNotificationChannel(fijo);
        nm.createNotificationChannel(avisos);
    }

    // ------------------------------------------------------------------ llamada en curso

    /** Mientras hay una llamada (saliente marcando o en conversación) no corre el tiempo muerto. */
    @SuppressWarnings("deprecation")
    private void escucharLlamadas() {
        TelephonyManager tm = getSystemService(TelephonyManager.class);
        if (tm == null || !RegistroTelefono.permisoTelefono(this)) return;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                TelephonyCallback cb = new EscuchaNueva();
                tm.registerTelephonyCallback(h::post, cb);
                escucha = cb;
            } else {
                PhoneStateListener l = new PhoneStateListener() {
                    @Override
                    public void onCallStateChanged(int estado, String numero) {
                        cambioDeEstado(estado);
                    }
                };
                tm.listen(l, PhoneStateListener.LISTEN_CALL_STATE);
                escucha = l;
            }
        } catch (SecurityException ignorada) {
            // sin permiso del teléfono: se guía solo por el historial
        }
    }

    private final class EscuchaNueva extends TelephonyCallback implements TelephonyCallback.CallStateListener {
        @Override
        public void onCallStateChanged(int estado) {
            cambioDeEstado(estado);
        }
    }

    @SuppressWarnings("deprecation")
    private void dejarDeEscucharLlamadas() {
        TelephonyManager tm = getSystemService(TelephonyManager.class);
        if (tm == null || escucha == null) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && escucha instanceof TelephonyCallback) tm.unregisterTelephonyCallback((TelephonyCallback) escucha);
        else if (escucha instanceof PhoneStateListener) tm.listen((PhoneStateListener) escucha, PhoneStateListener.LISTEN_NONE);
    }

    private void cambioDeEstado(int estado) {
        boolean antes = enLlamada;
        enLlamada = estado == TelephonyManager.CALL_STATE_OFFHOOK;
        // Al colgar, la llamada puede tardar un momento en aparecer en el historial: se guarda la hora de fin.
        if (antes && !enLlamada) prefs(this).edit().putLong("fin-llamada", System.currentTimeMillis()).apply();
        pedirRevision(enLlamada ? 0 : 3000);
    }

    private void pedirRevision(long espera) {
        if (h == null) return;
        h.removeCallbacks(revisar);
        h.postDelayed(revisar, espera);
    }

    // ------------------------------------------------------------------ la cuenta

    private void revisar() {
        try {
            enviarPendientes();
            AlarmManager am = getSystemService(AlarmManager.class);
            if (!debeVigilar(this)) {
                am.cancel(alarma(this));
                stopSelf();
                return;
            }
            if (enLlamada) {
                am.cancel(alarma(this));
                return;
            }
            long ahora = System.currentTimeMillis();
            long ini = hoyA(INICIO), fin = hoyA(FIN), a0 = hoyA(ALMUERZO_INI), a1 = hoyA(ALMUERZO_FIN);
            Calendar cal = Calendar.getInstance();
            boolean laborable = cal.get(Calendar.DAY_OF_WEEK) != Calendar.SUNDAY;
            if (!laborable || ahora >= fin) {
                programar(am, siguienteInicio());
                return;
            }
            if (ahora < ini) {
                programar(am, ini);
                return;
            }
            long base = Math.min(ahora, Math.max(ini, ultimaActividad(ini)));
            long ocioso = efectivo(base, ahora, a0, a1);

            SharedPreferences p = prefs(this);
            int n = p.getLong("pausa-base", 0) == base ? p.getInt("pausa-avisos", 0) : 0;
            if (ocioso >= umbral(n)) {
                avisar(base, ini, ocioso, n);
                // Un solo aviso aunque se hayan pasado varios umbrales (p. ej. el teléfono estuvo apagado).
                while (ocioso >= umbral(n)) n++;
            }
            p.edit().putLong("pausa-base", base).putInt("pausa-avisos", n).apply();

            long cuando = momentoEn(ahora, umbral(n) - ocioso, a0, a1) + 1000;
            programar(am, cuando >= fin ? siguienteInicio() : cuando);
        } catch (Exception e) {
            android.util.Log.w("CALLFLOW", "Vigilante: " + e);
            pedirRevision(5 * MIN);
        }
    }

    private static long umbral(int avisosDados) {
        return (LIMITE_MIN + (long) REPETIR_MIN * avisosDados) * MIN;
    }

    /** Fin de la última llamada de hoy que cuenta como actividad (o de la última que se colgó). */
    private long ultimaActividad(long inicioHoy) throws Exception {
        String hoy = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(System.currentTimeMillis());
        JSONObject r = RegistroTelefono.consultar(this, hoy, hoy, true, chipsTrabajo());
        JSONArray ls = r.getJSONArray("llamadas");
        long ultima = 0, ahora = System.currentTimeMillis();
        for (int i = 0; i < ls.length(); i++) {
            JSONObject l = ls.getJSONObject(i);
            long d = l.optLong("d"), t = l.getLong("t");
            if (t > ahora) continue;  // hora en el futuro: el reloj del teléfono se cambió
            if ("saliente".equals(l.optString("tp")) || d > 0) ultima = Math.max(ultima, t + d * 1000);
        }
        long colgada = prefs(this).getLong("fin-llamada", 0);
        if (colgada >= inicioHoy) ultima = Math.max(ultima, colgada);
        return ultima;
    }

    private Set<String> chipsTrabajo() {
        Set<String> s = new HashSet<>();
        try {
            JSONArray a = new JSONArray(prefs(this).getString("chips-trabajo", "[]"));
            for (int i = 0; i < a.length(); i++) s.add(a.getString(i));
        } catch (Exception ignorada) {
            // vacío = todos los chips
        }
        return s;
    }

    /** Milisegundos entre a y b sin contar el almuerzo. */
    static long efectivo(long a, long b, long a0, long a1) {
        return Math.max(0, b - a) - Math.max(0, Math.min(b, a1) - Math.max(a, a0));
    }

    /** Hora en que, desde t, se cumplen «falta» ms sin contar el almuerzo. */
    static long momentoEn(long t, long falta, long a0, long a1) {
        if (t >= a0 && t < a1) t = a1;
        long r = t + Math.max(0, falta);
        if (t < a0 && r > a0) r += a1 - a0;
        return r;
    }

    private static long hoyA(String hhmm) {
        Calendar c = Calendar.getInstance();
        c.set(Calendar.HOUR_OF_DAY, Integer.parseInt(hhmm.substring(0, 2)));
        c.set(Calendar.MINUTE, Integer.parseInt(hhmm.substring(3, 5)));
        c.set(Calendar.SECOND, 0);
        c.set(Calendar.MILLISECOND, 0);
        return c.getTimeInMillis();
    }

    /** 9:00 del próximo día laborable (lunes a sábado). */
    private static long siguienteInicio() {
        Calendar c = Calendar.getInstance();
        c.setTimeInMillis(hoyA(INICIO));
        do c.add(Calendar.DAY_OF_MONTH, 1); while (c.get(Calendar.DAY_OF_WEEK) == Calendar.SUNDAY);
        return c.getTimeInMillis();
    }

    private void programar(AlarmManager am, long cuando) {
        PendingIntent pi = alarma(this);
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, cuando, pi);
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, cuando, pi);
    }

    // ------------------------------------------------------------------ avisos

    private void avisar(long base, long inicioHoy, long ocioso, int n) {
        long min = ocioso / MIN;
        String hora = new SimpleDateFormat("HH:mm", Locale.US).format(base);
        String asesor = prefs(this).getString("asesor-nombre", "Asesor");
        String detalle = base <= inicioHoy ? "No ha llamado desde las " + hora + " (inicio de la jornada)."
                : "Su última llamada terminó a las " + hora + ".";

        // En el teléfono del asesor
        Notification noti = new Notification.Builder(this, CANAL_AVISOS)
                .setSmallIcon(android.R.drawable.stat_sys_warning)
                .setContentTitle("Lleva " + min + " min sin llamar")
                .setContentText("Haga su siguiente llamada. El supervisor también recibe este aviso.")
                .setStyle(new Notification.BigTextStyle().bigText(detalle + " Haga su siguiente llamada. El supervisor también recibe este aviso."))
                .setContentIntent(abrirApp())
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_REMINDER)
                .build();
        try {
            getSystemService(NotificationManager.class).notify(NOTI_AVISO, noti);
        } catch (SecurityException ignorada) {
            // sin permiso de notificaciones: el supervisor igual recibe el aviso
        }

        // Al supervisor, por ntfy. Solo nombre y minutos: nada de números de clientes.
        try {
            JSONObject m = new JSONObject().put("topic", TemaAvisos.VALOR)
                    .put("title", asesor + ": " + min + " min sin llamar")
                    .put("message", detalle.replace("Su última", "La última") + (n > 0 ? " Sigue sin llamar (aviso " + (n + 1) + ")." : ""))
                    .put("priority", n > 0 ? 5 : 4)
                    .put("tags", new JSONArray().put(n > 0 ? "rotating_light" : "warning"));
            if (!publicar(m)) guardarPendiente(m);
        } catch (Exception ignorada) {
            // no debería pasar
        }
    }

    /** Publica un mensaje en ntfy (formato JSON). true si el servidor lo aceptó. */
    static boolean publicar(JSONObject mensaje) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(SERVIDOR).openConnection();
            c.setConnectTimeout(10_000);
            c.setReadTimeout(10_000);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            try (OutputStream out = c.getOutputStream()) {
                out.write(mensaje.toString().getBytes(StandardCharsets.UTF_8));
            }
            return c.getResponseCode() / 100 == 2;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** Sin internet: el aviso se guarda y sale en la próxima revisión (si no tiene más de 30 min). */
    private void guardarPendiente(JSONObject m) {
        try {
            SharedPreferences p = prefs(this);
            JSONArray a = new JSONArray(p.getString("ntfy-pendientes", "[]"));
            a.put(new JSONObject().put("cuando", System.currentTimeMillis()).put("m", m));
            while (a.length() > 20) a.remove(0);
            p.edit().putString("ntfy-pendientes", a.toString()).apply();
        } catch (Exception ignorada) {
            // se pierde este aviso
        }
    }

    private void enviarPendientes() {
        SharedPreferences p = prefs(this);
        String guardado = p.getString("ntfy-pendientes", "[]");
        if ("[]".equals(guardado)) return;
        try {
            JSONArray a = new JSONArray(guardado), quedan = new JSONArray();
            long limite = System.currentTimeMillis() - 30 * MIN;
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                if (o.getLong("cuando") < limite) continue;
                JSONObject m = new JSONObject(o.getJSONObject("m").toString());  // copia: el guardado no cambia
                String hora = new SimpleDateFormat("HH:mm", Locale.US).format(o.getLong("cuando"));
                m.put("message", m.optString("message") + " (aviso de las " + hora + ", el teléfono estaba sin internet)");
                if (!publicar(m)) quedan.put(o);
            }
            p.edit().putString("ntfy-pendientes", quedan.toString()).apply();
        } catch (Exception e) {
            p.edit().putString("ntfy-pendientes", "[]").apply();
        }
    }
}
