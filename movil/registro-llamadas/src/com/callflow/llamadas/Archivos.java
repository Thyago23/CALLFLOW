package com.callflow.llamadas;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;
import android.provider.OpenableColumns;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/** Guardar en Descargas/CALLFLOW y leer archivos que llegan de otras apps (WhatsApp, Archivos). */
final class Archivos {
    private Archivos() {
    }

    static final class Guardado {
        final Uri uri;
        final String nombre;

        Guardado(Uri uri, String nombre) {
            this.uri = uri;
            this.nombre = nombre;
        }
    }

    static Guardado guardar(Context ctx, String nombre, String mime, byte[] datos) throws Exception {
        nombre = limpiarNombre(nombre);
        ContentResolver cr = ctx.getContentResolver();
        ContentValues v = new ContentValues();
        v.put(MediaStore.Downloads.DISPLAY_NAME, nombre);
        v.put(MediaStore.Downloads.MIME_TYPE, mime);
        v.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/CALLFLOW");
        v.put(MediaStore.Downloads.IS_PENDING, 1);
        Uri uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
        if (uri == null) throw new IllegalStateException("No se pudo crear el archivo en Descargas.");
        try (OutputStream salida = cr.openOutputStream(uri)) {
            if (salida == null) throw new IllegalStateException("No se pudo escribir el archivo.");
            salida.write(datos);
        } catch (Exception e) {
            cr.delete(uri, null, null);  // no deja un archivo a medias
            throw e;
        }
        v.clear();
        v.put(MediaStore.Downloads.IS_PENDING, 0);
        cr.update(uri, v, null, null);
        String real = nombre(ctx, uri);  // si ya existía, Android le agrega « (1)»
        return new Guardado(uri, real.isEmpty() ? nombre : real);
    }

    static byte[] leer(Context ctx, Uri uri, int maximo) throws IOException {
        try (InputStream in = ctx.getContentResolver().openInputStream(uri)) {
            if (in == null) throw new IOException("No se pudo abrir el archivo.");
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > maximo) throw new IOException("El archivo es demasiado grande.");
            }
            return out.toByteArray();
        }
    }

    static String nombre(Context ctx, Uri uri) {
        try (Cursor c = ctx.getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst() && c.getString(0) != null) return c.getString(0);
        } catch (Exception ignorada) {
            // sigue abajo
        }
        String s = uri.getLastPathSegment();
        return s == null ? "" : s;
    }

    static String limpiarNombre(String s) {
        return s.replaceAll("[\\\\/:*?\"<>|]", "-").trim();
    }
}
