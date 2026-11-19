package com.callflow.llamadas;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.os.Build;
import android.provider.CallLog;
import android.telecom.PhoneAccount;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;
import android.telephony.SubscriptionInfo;
import android.telephony.SubscriptionManager;
import android.telephony.TelephonyManager;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Lee el historial de llamadas del teléfono (permiso READ_CALL_LOG) y mantiene una copia propia en la app.
 * La copia sirve para dos cosas: que el reporte no dependa de que el asesor no borre llamadas del historial
 * (las borradas se siguen reportando, marcadas) y que no se pierdan las antiguas cuando el teléfono recorta su historial.
 * Con dos chips, cada llamada lleva el chip por el que se hizo (CallLog.Calls.PHONE_ACCOUNT_ID).
 */
final class RegistroTelefono {
    private static final String ARCHIVO = "copia_llamadas.json";
    private static final long DIA = 86_400_000L, RETENCION = 120 * DIA;  // la copia guarda los últimos 120 días
    static final String HORA_INICIO = "09:00", HORA_FIN = "18:00";
    private static final String[] COLUMNAS = {CallLog.Calls.DATE, CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME,
            CallLog.Calls.TYPE, CallLog.Calls.DURATION, CallLog.Calls.PHONE_ACCOUNT_ID};

    private RegistroTelefono() {
    }

    static boolean permisoLlamadas(Context c) {
        return c.checkSelfPermission(Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED;
    }

    static boolean permisoTelefono(Context c) {
        return c.checkSelfPermission(Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED;
    }

    /** Una línea del teléfono que puede llamar (un chip). id = el PHONE_ACCOUNT_ID que queda en el historial. */
    static final class Chip {
        String id, nombre, detalle = "";
        int ranura;

        JSONObject json() throws JSONException {
            return new JSONObject().put("id", id).put("nombre", nombre).put("detalle", detalle).put("ranura", ranura);
        }
    }

    /** Chips activos, ordenados por ranura («Chip 1», «Chip 2»). Vacío si falta el permiso del teléfono. */
    static List<Chip> chips(Context ctx) {
        List<Chip> lista = new ArrayList<>();
        if (!permisoTelefono(ctx)) return lista;
        try {
            TelecomManager tm = ctx.getSystemService(TelecomManager.class);
            TelephonyManager tel = ctx.getSystemService(TelephonyManager.class);
            SubscriptionManager sm = ctx.getSystemService(SubscriptionManager.class);
            List<SubscriptionInfo> subs = sm.getActiveSubscriptionInfoList();
            if (subs == null) subs = Collections.emptyList();
            int orden = 0;
            for (PhoneAccountHandle h : tm.getCallCapablePhoneAccounts()) {
                orden++;
                Chip ch = new Chip();
                ch.id = h.getId();
                ch.ranura = orden;
                SubscriptionInfo si = null;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    int sub = tel.getSubscriptionId(h);
                    for (SubscriptionInfo s : subs) if (s.getSubscriptionId() == sub) si = s;
                }
                if (si == null) {  // Android 10, o fabricantes que guardan el id de la suscripción o el ICCID
                    for (SubscriptionInfo s : subs) {
                        String icc = s.getIccId();
                        if (String.valueOf(s.getSubscriptionId()).equals(h.getId())
                                || (icc != null && !icc.isEmpty() && h.getId().startsWith(icc))) si = s;
                    }
                }
                List<String> partes = new ArrayList<>();
                if (si != null) {
                    ch.ranura = si.getSimSlotIndex() + 1;
                    agregarDistinto(partes, si.getDisplayName());
                    agregarDistinto(partes, si.getCarrierName());
                } else {
                    PhoneAccount pa = tm.getPhoneAccount(h);
                    if (pa != null) agregarDistinto(partes, pa.getLabel());
                }
                ch.detalle = String.join(" · ", partes);
                ch.nombre = "Chip " + ch.ranura;
                lista.add(ch);
            }
        } catch (SecurityException ignorada) {
            // sin permiso: lista vacía
        }
        lista.sort((a, b) -> a.ranura - b.ranura);
        return lista;
    }

    private static void agregarDistinto(List<String> partes, CharSequence texto) {
        if (texto == null) return;
        String t = texto.toString().trim();
        if (t.isEmpty()) return;
        for (String p : partes) if (p.equalsIgnoreCase(t)) return;
        partes.add(t);
    }

    /**
     * Pasa el historial del teléfono a la copia propia. Las llamadas que estaban en la copia y ya no están en el
     * historial (dentro del periodo que el historial todavía cubre) quedan marcadas como borradas.
     */
    static synchronized JSONObject sincronizar(Context ctx) throws Exception {
        JSONObject archivo = cargar(ctx);
        JSONObject mapa = archivo.optJSONObject("llamadas");
        if (mapa == null) mapa = new JSONObject();
        if (!permisoLlamadas(ctx)) return archivo;

        long ahora = System.currentTimeMillis(), limite = ahora - RETENCION;
        long masAntigua = Long.MIN_VALUE;
        boolean hayAlguna = false;
        Set<String> vistas = new HashSet<>();
        try (Cursor cur = ctx.getContentResolver().query(CallLog.Calls.CONTENT_URI, COLUMNAS, null, null, CallLog.Calls.DATE + " ASC")) {
            if (cur != null) {
                while (cur.moveToNext()) {
                    long t = cur.getLong(0);
                    if (!hayAlguna) { masAntigua = t; hayAlguna = true; }
                    if (t < limite) continue;
                    String numero = normalizar(cur.getString(1));
                    int tipo = cur.getInt(3);
                    String clave = t + "|" + numero + "|" + tipo;
                    vistas.add(clave);
                    JSONObject o = mapa.optJSONObject(clave);
                    if (o == null) { o = new JSONObject(); mapa.put(clave, o); }
                    o.put("t", t).put("n", numero).put("c", texto(cur.getString(2))).put("tp", tipo)
                            .put("d", cur.getLong(4)).put("cu", texto(cur.getString(5))).put("b", 0);
                }
            }
        }
        List<String> viejas = new ArrayList<>();
        for (Iterator<String> it = mapa.keys(); it.hasNext(); ) {
            String k = it.next();
            JSONObject o = mapa.getJSONObject(k);
            long t = o.getLong("t");
            if (t < limite) viejas.add(k);
            // Historial vacío del todo = lo borraron completo: todo lo copiado cuenta como borrado.
            else if (!vistas.contains(k) && (!hayAlguna || t >= masAntigua)) o.put("b", 1);
        }
        for (String k : viejas) mapa.remove(k);
        if (!archivo.has("desde")) archivo.put("desde", ahora);
        archivo.put("llamadas", mapa).put("sincronizado", ahora);
        guardar(ctx, archivo);
        return archivo;
    }

    /**
     * Llamadas de los días desde..hasta (AAAA-MM-DD, ambos incluidos), solo de los chips de trabajo (si hay
     * elegidos) y, si soloHorario, solo de 9:00 a 18:00. Ordenadas de la más antigua a la más reciente.
     */
    static JSONObject consultar(Context ctx, String desde, String hasta, boolean soloHorario, Set<String> chipsTrabajo) throws Exception {
        JSONObject r = new JSONObject();
        r.put("permiso", permisoLlamadas(ctx)).put("permisoTelefono", permisoTelefono(ctx));
        JSONObject archivo = sincronizar(ctx);
        JSONObject mapa = archivo.optJSONObject("llamadas");
        if (mapa == null) mapa = new JSONObject();

        List<Chip> chips = chips(ctx);
        Map<String, Chip> porId = new HashMap<>();
        JSONArray chipsJson = new JSONArray(), trabajoJson = new JSONArray();
        for (Chip ch : chips) {
            porId.put(ch.id, ch);
            boolean deTrabajo = chipsTrabajo.isEmpty() || chipsTrabajo.contains(ch.id);
            chipsJson.put(ch.json().put("trabajo", deTrabajo));
            if (deTrabajo) trabajoJson.put(ch.json());
        }

        SimpleDateFormat dia = new SimpleDateFormat("yyyy-MM-dd", Locale.US), hora = new SimpleDateFormat("HH:mm", Locale.US);
        long ini = inicioDelDia(desde), fin = inicioDelDia(hasta) + DIA;
        List<JSONObject> lista = new ArrayList<>();
        for (Iterator<String> it = mapa.keys(); it.hasNext(); ) {
            JSONObject o = mapa.getJSONObject(it.next());
            long t = o.getLong("t");
            if (t < ini || t >= fin) continue;
            String hh = hora.format(t);
            if (soloHorario && (hh.compareTo(HORA_INICIO) < 0 || hh.compareTo(HORA_FIN) > 0)) continue;
            String cu = o.optString("cu");
            Chip ch = porId.get(cu);
            if (ch == null && cu.isEmpty() && chips.size() == 1) ch = chips.get(0);  // un solo chip: es ese
            if (ch != null && !chipsTrabajo.isEmpty() && !chipsTrabajo.contains(ch.id)) continue;  // chip personal
            String chip = ch != null ? ch.nombre : (cu.isEmpty() ? "Sin identificar" : "Otro chip");
            lista.add(new JSONObject().put("t", t).put("fecha", dia.format(t)).put("hora", hh).put("n", o.optString("n"))
                    .put("c", o.optString("c")).put("tp", tipoTexto(o.optInt("tp"))).put("d", o.optLong("d"))
                    .put("chip", chip).put("b", o.optInt("b")));
        }
        lista.sort((a, b) -> Long.compare(a.optLong("t"), b.optLong("t")));
        JSONArray llamadas = new JSONArray();
        int borradas = 0;
        for (JSONObject o : lista) {
            llamadas.put(o);
            borradas += o.optInt("b");
        }
        r.put("desde", desde).put("hasta", hasta).put("soloHorario", soloHorario)
                .put("chips", chipsJson).put("chipsTrabajo", trabajoJson)
                .put("llamadas", llamadas).put("borradas", borradas)
                .put("copiaDesde", archivo.optLong("desde", 0));
        return r;
    }

    static String tipoTexto(int tipo) {
        switch (tipo) {
            case CallLog.Calls.OUTGOING_TYPE: return "saliente";
            case CallLog.Calls.INCOMING_TYPE:
            case CallLog.Calls.ANSWERED_EXTERNALLY_TYPE: return "entrante";
            case CallLog.Calls.MISSED_TYPE: return "perdida";
            case CallLog.Calls.REJECTED_TYPE:
            case CallLog.Calls.BLOCKED_TYPE: return "rechazada";
            default: return "otra";
        }
    }

    /** +593 99 123 4567 → 0991234567; sin número (privado) → «Número oculto». */
    static String normalizar(String n) {
        if (n == null) return "Número oculto";
        String s = n.replaceAll("[\\s\\-().]", "");
        if (s.isEmpty() || s.startsWith("-")) return "Número oculto";
        if (s.matches("\\+?593\\d{9}")) s = "0" + s.replaceFirst("^\\+?593", "");
        return s;
    }

    private static String texto(String s) {
        return s == null ? "" : s.trim();
    }

    private static long inicioDelDia(String iso) {
        Calendar cal = Calendar.getInstance();
        cal.clear();
        cal.set(Integer.parseInt(iso.substring(0, 4)), Integer.parseInt(iso.substring(5, 7)) - 1, Integer.parseInt(iso.substring(8, 10)));
        return cal.getTimeInMillis();
    }

    private static JSONObject cargar(Context ctx) {
        File f = new File(ctx.getFilesDir(), ARCHIVO);
        if (!f.exists()) return new JSONObject();
        try {
            return new JSONObject(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8));
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    /** Escribe en un archivo temporal y lo renombra: un corte a medias no deja la copia dañada. */
    private static void guardar(Context ctx, JSONObject archivo) throws Exception {
        File f = new File(ctx.getFilesDir(), ARCHIVO), tmp = new File(ctx.getFilesDir(), ARCHIVO + ".tmp");
        try (FileOutputStream out = new FileOutputStream(tmp)) {
            out.write(archivo.toString().getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        }
        if (!tmp.renameTo(f)) throw new IllegalStateException("No se pudo guardar la copia de llamadas.");
    }
}
