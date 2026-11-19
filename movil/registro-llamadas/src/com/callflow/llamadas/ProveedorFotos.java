package com.callflow.llamadas;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * Entrega a la app de cámara un lugar donde dejar la foto del registro de llamadas (sin pedir permisos ni usar la
 * galería). Hay una sola foto, en la caché de la app: cada foto nueva reemplaza a la anterior.
 * No se exporta: la cámara solo puede escribir con el permiso temporal que le da el Intent.
 */
public class ProveedorFotos extends ContentProvider {
    static final String AUTORIDAD = "com.callflow.llamadas.fotos";

    static File archivo(Context c) {
        File carpeta = new File(c.getCacheDir(), "fotos");
        //noinspection ResultOfMethodCallIgnored
        carpeta.mkdirs();
        return new File(carpeta, "foto.jpg");
    }

    /** Borra la foto anterior y devuelve la dirección para la nueva (con la hora, para que el WebView no use una copia vieja). */
    static Uri nuevaFoto(Context c) {
        //noinspection ResultOfMethodCallIgnored
        archivo(c).delete();
        return Uri.parse("content://" + AUTORIDAD + "/foto-" + System.currentTimeMillis() + ".jpg");
    }

    static boolean hayFoto(Context c) {
        File f = archivo(c);
        return f.exists() && f.length() > 0;
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String modo) throws FileNotFoundException {
        return ParcelFileDescriptor.open(archivo(getContext()), ParcelFileDescriptor.parseMode(modo));
    }

    @Override
    public String getType(Uri uri) {
        return "image/jpeg";
    }

    @Override
    public Cursor query(Uri uri, String[] columnas, String seleccion, String[] args, String orden) {
        MatrixCursor c = new MatrixCursor(new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        c.addRow(new Object[]{"foto.jpg", archivo(getContext()).length()});
        return c;
    }

    @Override
    public Uri insert(Uri uri, ContentValues valores) {
        return null;
    }

    @Override
    public int delete(Uri uri, String seleccion, String[] args) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues valores, String seleccion, String[] args) {
        return 0;
    }
}
