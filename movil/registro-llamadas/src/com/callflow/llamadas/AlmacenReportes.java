package com.callflow.llamadas;

import android.content.Context;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Reportes de los asesores recibidos en el teléfono del supervisor (archivo privado de la app).
 * Cada reporte es el JSON que viene dentro del PDF (ver Sello) más «recibido» y «archivo».
 */
final class AlmacenReportes {
    private static final String ARCHIVO = "reportes_asesores.json";
    private static final int MAXIMO = 40 * 1024 * 1024;

    private AlmacenReportes() {
    }

    static synchronized JSONArray todos(Context ctx) {
        File f = new File(ctx.getFilesDir(), ARCHIVO);
        if (!f.exists()) return new JSONArray();
        try {
            return new JSONArray(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    private static void guardarTodos(Context ctx, JSONArray lista) throws Exception {
        File f = new File(ctx.getFilesDir(), ARCHIVO), tmp = new File(ctx.getFilesDir(), ARCHIVO + ".tmp");
        try (FileOutputStream out = new FileOutputStream(tmp)) {
            out.write(lista.toString().getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        }
        if (!tmp.renameTo(f)) throw new IllegalStateException("No se pudieron guardar los reportes.");
    }

    /** Devuelve {agregados:[…], repetidos:[…], errores:[{archivo, error}]}. */
    static synchronized JSONObject importar(Context ctx, List<Uri> uris) {
        JSONObject r = new JSONObject();
        JSONArray agregados = new JSONArray(), repetidos = new JSONArray(), errores = new JSONArray();
        try {
            JSONArray lista = todos(ctx);
            Set<String> ids = new HashSet<>();
            for (int i = 0; i < lista.length(); i++) ids.add(lista.getJSONObject(i).optString("id"));
            for (Uri uri : uris) {
                String nombre = Archivos.nombre(ctx, uri);
                try {
                    Sello.Extraido e = Sello.extraer(Archivos.leer(ctx, uri, MAXIMO));
                    if (e == null) throw new IllegalArgumentException("No trae datos de llamadas: debe ser el PDF que genera el asesor en la app (versión 2.0 o superior).");
                    if (!e.valido) throw new IllegalArgumentException("El reporte fue modificado después de generarse. Pida al asesor que lo genere de nuevo.");
                    JSONObject d = new JSONObject(e.json);
                    if (!"reporte-asesor".equals(d.optString("tipo"))) throw new IllegalArgumentException("No es un reporte de asesor.");
                    if (ids.contains(d.optString("id"))) {
                        repetidos.put(resumen(d));
                        continue;
                    }
                    d.put("recibido", System.currentTimeMillis()).put("archivo", nombre);
                    lista.put(d);
                    ids.add(d.optString("id"));
                    agregados.put(resumen(d));
                } catch (Exception ex) {
                    String m = ex instanceof SecurityException ? "La app no tiene permiso para abrir ese archivo. Compártalo desde WhatsApp o elíjalo con «Agregar PDF»."
                            : ex.getMessage() != null ? ex.getMessage() : "No se pudo leer.";
                    errores.put(new JSONObject().put("archivo", nombre).put("error", m));
                }
            }
            if (agregados.length() > 0) guardarTodos(ctx, lista);
            r.put("agregados", agregados).put("repetidos", repetidos).put("errores", errores);
        } catch (Exception e) {
            try {
                r.put("agregados", new JSONArray()).put("repetidos", repetidos)
                        .put("errores", errores.put(new JSONObject().put("archivo", "").put("error", String.valueOf(e.getMessage()))));
            } catch (Exception ignorada) {
                // no pasa
            }
        }
        return r;
    }

    static synchronized void quitar(Context ctx, String id) throws Exception {
        JSONArray lista = todos(ctx), nueva = new JSONArray();
        for (int i = 0; i < lista.length(); i++) if (!id.equals(lista.getJSONObject(i).optString("id"))) nueva.put(lista.get(i));
        guardarTodos(ctx, nueva);
    }

    private static JSONObject resumen(JSONObject d) throws Exception {
        JSONArray ll = d.optJSONArray("llamadas");
        return new JSONObject().put("asesor", d.optString("asesor")).put("desde", d.optString("desde"))
                .put("hasta", d.optString("hasta")).put("llamadas", ll == null ? 0 : ll.length());
    }
}
