package com.callflow.llamadas;

import android.util.Base64;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Datos exactos dentro del PDF del asesor, para que el supervisor los una sin leer el texto del PDF.
 *
 * Los datos (JSON comprimido, en base64) van como comentarios PDF («%CFD …») después del final del archivo y luego
 * se repite el «startxref … %%EOF» original: los lectores de PDF siguen encontrando la misma tabla y el PDF se ve
 * igual. La firma HMAC («%CFS …») detecta si alguien cambió los datos. No es infalible (la llave va dentro del APK),
 * pero impide editar el reporte con un editor de PDF o de texto. Todos los teléfonos deben tener un APK compilado con
 * la misma llave (firma\sello.txt) para que el supervisor acepte los reportes.
 */
final class Sello {
    /** La llave no está en el repositorio: build.ps1 genera LlaveSello.java desde firma\sello.txt. */
    private static final byte[] LLAVE = LlaveSello.VALOR.getBytes(StandardCharsets.UTF_8);
    static final String INICIO = "%CALLFLOW-DATOS-V1", FIN = "%CALLFLOW-FIN";

    private Sello() {
    }

    static final class Extraido {
        final String json;
        final boolean valido;

        Extraido(String json, boolean valido) {
            this.json = json;
            this.valido = valido;
        }
    }

    static String firma(String json) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(LLAVE, "HmacSHA256"));
        byte[] h = mac.doFinal(json.getBytes(StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        for (byte b : h) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    /** Código corto que se imprime en el PDF: 4F2A-91C0-7B3E. */
    static String codigo(String firma) {
        String s = firma.substring(0, 12).toUpperCase();
        return s.substring(0, 4) + "-" + s.substring(4, 8) + "-" + s.substring(8, 12);
    }

    static byte[] incrustar(byte[] pdf, String json, String firma) throws IOException {
        String texto = new String(pdf, StandardCharsets.ISO_8859_1);
        int sx = texto.lastIndexOf("startxref");
        if (sx < 0) throw new IOException("El PDF no tiene startxref.");
        ByteArrayOutputStream comprimido = new ByteArrayOutputStream();
        try (GZIPOutputStream gz = new GZIPOutputStream(comprimido)) {
            gz.write(json.getBytes(StandardCharsets.UTF_8));
        }
        String b64 = Base64.encodeToString(comprimido.toByteArray(), Base64.NO_WRAP);
        StringBuilder sb = new StringBuilder("\n").append(INICIO).append('\n');
        for (int i = 0; i < b64.length(); i += 76) sb.append("%CFD ").append(b64, i, Math.min(b64.length(), i + 76)).append('\n');
        sb.append("%CFS ").append(firma).append('\n').append(FIN).append('\n');
        ByteArrayOutputStream salida = new ByteArrayOutputStream(pdf.length + sb.length() + 64);
        salida.write(pdf);
        salida.write(sb.toString().getBytes(StandardCharsets.ISO_8859_1));
        salida.write(texto.substring(sx).getBytes(StandardCharsets.ISO_8859_1));
        return salida.toByteArray();
    }

    /** null si el PDF no trae datos (no es un reporte de la app). */
    static Extraido extraer(byte[] pdf) throws Exception {
        String texto = new String(pdf, StandardCharsets.ISO_8859_1);
        int i = texto.lastIndexOf(INICIO);
        if (i < 0) return null;
        int f = texto.indexOf(FIN, i);
        if (f < 0) return null;
        StringBuilder b64 = new StringBuilder();
        String firmaLeida = "";
        for (String linea : texto.substring(i, f).split("\n")) {
            linea = linea.trim();
            if (linea.startsWith("%CFD ")) b64.append(linea.substring(5).trim());
            else if (linea.startsWith("%CFS ")) firmaLeida = linea.substring(5).trim();
        }
        byte[] gz = Base64.decode(b64.toString(), Base64.DEFAULT);
        ByteArrayOutputStream plano = new ByteArrayOutputStream();
        try (InputStream in = new GZIPInputStream(new ByteArrayInputStream(gz))) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) plano.write(buf, 0, n);
        }
        String json = plano.toString("UTF-8");
        boolean valido = MessageDigest.isEqual(firma(json).getBytes(StandardCharsets.US_ASCII),
                firmaLeida.getBytes(StandardCharsets.US_ASCII));
        return new Extraido(json, valido);
    }
}
