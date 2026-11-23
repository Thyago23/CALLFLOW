package com.callflow.llamadas;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Picture;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.pdf.PdfDocument;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * PDF a partir de una descripción JSON que arma la página (reporte del asesor y consolidado del supervisor):
 * <pre>
 * { "titulo": "…", "horizontal": false, "cabecera": ["en negrita", "normal", …], "pie": "…", "codigo": "…",
 *   "bloques": [ {"t":"titulo","texto":"…"},
 *                {"t":"kpis","items":[["Llamadas","45"], …]},
 *                {"t":"tabla","cols":[{"t":"Asesor","w":3,"a":"i|d|c","b":true}],"filas":[["…"]],"alertas":[índices],
 *                 "marcas":[[fila, columna], …]},   (marcas: celdas sueltas en rojo)
 *                {"t":"destacado","titulo":"…","items":[["Etiqueta","valor","detalle", rojo?], …],"nota":"…"},
 *                {"t":"nota","texto":"…","alerta":true} ] }
 * </pre>
 * Se dibuja dos veces: la primera en blanco solo para contar páginas («Página x de y»), la segunda en el PDF.
 */
final class PdfDocumento {
    private static final int MARGEN = 36, PIE = 24, FILA = 17, CAB_TABLA = 20;
    private static final int AZUL = Color.parseColor("#0062a8"), AZUL_SUAVE = Color.parseColor("#e8f2fa"), CEBRA = Color.parseColor("#f6f9fc");
    private static final int NOCHE = Color.parseColor("#0a2a4a"), ROSA = Color.parseColor("#ff0066"), ROJO = Color.parseColor("#c62828");
    private static final int GRIS = Color.parseColor("#5b6f84"), LINEA = Color.parseColor("#cfdceb"), AVISO = Color.parseColor("#fdecec");
    // Recuadro destacado (tiempo muerto): naranja para que salte a la vista.
    private static final int NARANJA = Color.parseColor("#e65100"), NARANJA_SUAVE = Color.parseColor("#fff3e0"), NARANJA_OSCURO = Color.parseColor("#a63a00");

    private PdfDocumento() {
    }

    static byte[] crear(Context ctx, JSONObject doc) throws Exception {
        Bitmap logo = logo(ctx);
        Maqueta prueba = new Maqueta(doc, logo, null, 0);
        prueba.dibujar();
        PdfDocument pdf = new PdfDocument();
        try {
            new Maqueta(doc, logo, pdf, prueba.paginas).dibujar();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            pdf.writeTo(out);
            return out.toByteArray();
        } finally {
            pdf.close();
        }
    }

    private static final class Maqueta {
        final JSONObject doc;
        final int ancho, alto, total;
        final Bitmap logo;
        final PdfDocument pdf;
        final Paint titulo = texto(18, true, NOCHE), subtitulo = texto(12, true, NOCHE), normal = texto(8.6f, false, NOCHE);
        final Paint negrita = texto(8.6f, true, NOCHE), rojo = texto(8.6f, false, ROJO), rojoNegrita = texto(8.6f, true, ROJO);
        final Paint suave = texto(8, false, GRIS), cabecera = texto(8, true, AZUL), linea1 = texto(10, true, NOCHE), linea = texto(9.5f, false, NOCHE);
        final Paint valor = texto(13, true, NOCHE), relleno = new Paint(Paint.ANTI_ALIAS_FLAG), trazo = new Paint();
        final Paint tituloDestacado = texto(9.5f, true, Color.WHITE), valorDestacado = texto(15, true, NARANJA_OSCURO), valorMal = texto(15, true, ROJO);
        final Paint etiquetaDestacado = texto(8, true, NOCHE), notaDestacado = texto(7.6f, false, NARANJA_OSCURO);
        final Paint bordeDestacado = new Paint(Paint.ANTI_ALIAS_FLAG);
        int paginas = 0, y;
        Canvas c;
        PdfDocument.Page pagina;
        Picture borrador;

        Maqueta(JSONObject doc, Bitmap logo, PdfDocument pdf, int total) {
            this.doc = doc;
            this.logo = logo;
            this.pdf = pdf;
            this.total = total;
            boolean horizontal = doc.optBoolean("horizontal");
            ancho = horizontal ? 842 : 595;
            alto = horizontal ? 595 : 842;
            trazo.setColor(LINEA);
            trazo.setStrokeWidth(0.6f);
            bordeDestacado.setColor(NARANJA);
            bordeDestacado.setStyle(Paint.Style.STROKE);
            bordeDestacado.setStrokeWidth(1.8f);
        }

        void dibujar() throws JSONException {
            nueva();
            encabezado();
            JSONArray bloques = doc.optJSONArray("bloques");
            if (bloques != null) {
                for (int i = 0; i < bloques.length(); i++) {
                    JSONObject b = bloques.getJSONObject(i);
                    switch (b.optString("t")) {
                        case "titulo": tituloSeccion(b.optString("texto")); break;
                        case "kpis": kpis(b.optJSONArray("items")); break;
                        case "tabla": tabla(b); break;
                        case "nota": nota(b.optString("texto"), b.optBoolean("alerta")); break;
                        case "destacado": destacado(b); break;
                        default: break;
                    }
                }
            }
            cerrar();
        }

        int derecha() {
            return ancho - MARGEN;
        }

        int contenido() {
            return ancho - 2 * MARGEN;
        }

        int limite() {
            return alto - MARGEN - PIE;
        }

        boolean cabe(int h) {
            return y + h <= limite();
        }

        void nueva() {
            paginas++;
            if (pdf != null) {
                pagina = pdf.startPage(new PdfDocument.PageInfo.Builder(ancho, alto, paginas).create());
                c = pagina.getCanvas();
            } else {
                borrador = new Picture();
                c = borrador.beginRecording(ancho, alto);
            }
            y = MARGEN;
        }

        void cerrar() {
            String codigo = doc.optString("codigo");
            String izq = doc.optString("pie") + (codigo.isEmpty() ? "" : "  ·  Código " + codigo);
            String der = "Página " + paginas + " de " + Math.max(total, paginas);
            c.drawLine(MARGEN, alto - MARGEN - 6, derecha(), alto - MARGEN - 6, trazo);
            c.drawText(recortar(izq, suave, contenido() - suave.measureText(der) - 20), MARGEN, alto - MARGEN + 8, suave);
            c.drawText(der, derecha() - suave.measureText(der), alto - MARGEN + 8, suave);
            if (pdf != null) pdf.finishPage(pagina);
            else borrador.endRecording();
        }

        void salto() {
            cerrar();
            nueva();
        }

        void encabezado() {
            int lado = 62;
            if (logo != null) {
                Paint suavizado = new Paint(Paint.FILTER_BITMAP_FLAG | Paint.ANTI_ALIAS_FLAG);
                c.drawBitmap(logo, null, new Rect(derecha() - lado, MARGEN - 8, derecha(), MARGEN - 8 + lado), suavizado);
            }
            c.drawText(doc.optString("titulo"), MARGEN, y + 18, titulo);
            relleno.setColor(ROSA);
            c.drawRect(MARGEN, y + 26, MARGEN + 48, y + 29, relleno);  // línea rosa de Multivisa
            y += 46;
            JSONArray lineas = doc.optJSONArray("cabecera");
            int anchoTexto = contenido() - lado - 12;
            if (lineas != null) {
                for (int i = 0; i < lineas.length(); i++) {
                    c.drawText(recortar(lineas.optString(i), i == 0 ? linea1 : linea, anchoTexto), MARGEN, y, i == 0 ? linea1 : linea);
                    y += 15;
                }
            }
            String codigo = doc.optString("codigo");
            if (!codigo.isEmpty()) {
                c.drawText("Código de verificación: " + codigo, MARGEN, y, suave);
                y += 15;
            }
            y = Math.max(y, MARGEN + lado) + 10;
        }

        void tituloSeccion(String texto) {
            if (!cabe(22 + CAB_TABLA + FILA * 2)) salto();  // el título no queda solo al pie de una página
            y += 6;
            c.drawText(texto, MARGEN, y + 12, subtitulo);
            y += 22;
        }

        void kpis(JSONArray items) {
            if (items == null) return;
            int porFila = ancho > alto ? 6 : 4, gap = 8, h = 42;
            float w = (contenido() - gap * (porFila - 1)) / (float) porFila;
            for (int i = 0; i < items.length(); i++) {
                int col = i % porFila;
                if (col == 0) {
                    if (i > 0) y += h + gap;
                    if (!cabe(h)) salto();
                }
                JSONArray it = items.optJSONArray(i);
                float x = MARGEN + col * (w + gap);
                relleno.setColor(AZUL_SUAVE);
                c.drawRoundRect(new RectF(x, y, x + w, y + h), 6, 6, relleno);
                c.drawText(recortar(it.optString(1), valor, w - 16), x + 8, y + 19, valor);
                c.drawText(recortar(it.optString(0), suave, w - 16), x + 8, y + 33, suave);
            }
            y += h + 14;
        }

        void tabla(JSONObject b) throws JSONException {
            JSONArray cols = b.getJSONArray("cols"), filas = b.optJSONArray("filas");
            int n = cols.length();
            float suma = 0;
            for (int k = 0; k < n; k++) suma += (float) cols.getJSONObject(k).optDouble("w", 1);
            float[] x = new float[n + 1];
            x[0] = MARGEN;
            for (int k = 0; k < n; k++) x[k + 1] = x[k] + contenido() * (float) cols.getJSONObject(k).optDouble("w", 1) / suma;
            Set<Integer> alertas = new HashSet<>();
            JSONArray a = b.optJSONArray("alertas");
            if (a != null) for (int i = 0; i < a.length(); i++) alertas.add(a.optInt(i));
            Set<Long> marcas = new HashSet<>();
            JSONArray m = b.optJSONArray("marcas");
            if (m != null) {
                for (int i = 0; i < m.length(); i++) {
                    JSONArray par = m.optJSONArray(i);
                    if (par != null) marcas.add(par.optLong(0) * 1000 + par.optLong(1));
                }
            }

            if (!cabe(CAB_TABLA + FILA)) salto();
            cabeceraTabla(cols, x);
            if (filas == null || filas.length() == 0) {
                c.drawText("Sin datos.", MARGEN + 6, y + 12, suave);
                y += FILA + 12;
                return;
            }
            for (int i = 0; i < filas.length(); i++) {
                if (!cabe(FILA)) {
                    salto();
                    cabeceraTabla(cols, x);
                }
                if (i % 2 == 1) {
                    relleno.setColor(CEBRA);
                    c.drawRect(MARGEN, y, derecha(), y + FILA, relleno);
                }
                JSONArray fila = filas.getJSONArray(i);
                boolean alerta = alertas.contains(i);
                for (int k = 0; k < n; k++) {
                    JSONObject col = cols.getJSONObject(k);
                    boolean bold = col.optBoolean("b");
                    Paint p = alerta ? (bold ? rojoNegrita : rojo) : (bold ? negrita : normal);
                    if (marcas.contains(i * 1000L + k)) {
                        relleno.setColor(AVISO);
                        c.drawRect(x[k] + 1, y + 1, x[k + 1] - 1, y + FILA - 1, relleno);
                        p = rojoNegrita;
                    }
                    celda(fila.optString(k), p, x[k], x[k + 1], col.optString("a"), y + 12);
                }
                y += FILA;
                c.drawLine(MARGEN, y, derecha(), y, trazo);
            }
            y += 14;
        }

        void cabeceraTabla(JSONArray cols, float[] x) throws JSONException {
            relleno.setColor(AZUL_SUAVE);
            c.drawRect(MARGEN, y, derecha(), y + CAB_TABLA, relleno);
            for (int k = 0; k < cols.length(); k++) {
                JSONObject col = cols.getJSONObject(k);
                celda(col.optString("t"), cabecera, x[k], x[k + 1], col.optString("a"), y + 13);
            }
            y += CAB_TABLA;
        }

        void celda(String s, Paint p, float x0, float x1, String alineacion, float base) {
            String t = recortar(s, p, x1 - x0 - 8);
            float w = p.measureText(t), x;
            if ("d".equals(alineacion)) x = x1 - 4 - w;
            else if ("c".equals(alineacion)) x = (x0 + x1 - w) / 2;
            else x = x0 + 4;
            c.drawText(t, x, base, p);
        }

        /** Recuadro con borde y banda naranja, cifras grandes y una nota al pie. No se parte entre páginas. */
        void destacado(JSONObject b) {
            JSONArray items = b.optJSONArray("items");
            int n = items == null ? 0 : items.length();
            int porFila = ancho > alto ? 6 : 3, pad = 10, gap = 6, banda = 22, celda = 46;
            int filas = (n + porFila - 1) / porFila;
            List<String> lineasNota = partir(b.optString("nota"), notaDestacado, contenido() - 2 * pad);
            if (b.optString("nota").isEmpty()) lineasNota.clear();
            int h = banda + pad + filas * celda + Math.max(0, filas - 1) * gap + (lineasNota.isEmpty() ? 0 : 6 + lineasNota.size() * 11) + pad;
            if (!cabe(h)) salto();
            float x0 = MARGEN, x1 = derecha();
            relleno.setColor(NARANJA_SUAVE);
            c.drawRoundRect(new RectF(x0, y, x1, y + h), 8, 8, relleno);
            relleno.setColor(NARANJA);
            c.drawRoundRect(new RectF(x0, y, x1, y + banda), 8, 8, relleno);
            c.drawRect(x0, y + banda - 8, x1, y + banda, relleno);  // esquinas de abajo de la banda, rectas
            c.drawText(recortar(b.optString("titulo").toUpperCase(), tituloDestacado, contenido() - 2 * pad), x0 + pad, y + 15, tituloDestacado);
            c.drawRoundRect(new RectF(x0, y, x1, y + h), 8, 8, bordeDestacado);

            float w = (contenido() - 2 * pad - gap * (porFila - 1)) / (float) porFila;
            int yy = y + banda + pad;
            for (int i = 0; i < n; i++) {
                int col = i % porFila;
                if (col == 0 && i > 0) yy += celda + gap;
                JSONArray it = items.optJSONArray(i);
                float x = x0 + pad + col * (w + gap);
                relleno.setColor(Color.WHITE);
                c.drawRoundRect(new RectF(x, yy, x + w, yy + celda), 6, 6, relleno);
                boolean mal = it.optBoolean(3);
                Paint v = mal ? valorMal : valorDestacado;
                c.drawText(recortar(it.optString(1), v, w - 14), x + 7, yy + 19, v);
                c.drawText(recortar(it.optString(0), etiquetaDestacado, w - 14), x + 7, yy + 31, etiquetaDestacado);
                c.drawText(recortar(it.optString(2), suave, w - 14), x + 7, yy + 41, suave);
            }
            yy += celda;
            if (!lineasNota.isEmpty()) {
                yy += 6;
                for (String l : lineasNota) {
                    yy += 11;
                    c.drawText(l, x0 + pad, yy - 2, notaDestacado);
                }
            }
            y += h + 12;
        }

        void nota(String texto, boolean alerta) {
            Paint p = alerta ? rojoNegrita : normal;
            List<String> lineas = partir(texto, p, contenido() - 20);
            int h = lineas.size() * 13 + 12;
            if (!cabe(h)) salto();
            relleno.setColor(alerta ? AVISO : AZUL_SUAVE);
            c.drawRoundRect(new RectF(MARGEN, y, derecha(), y + h), 6, 6, relleno);
            int yy = y + 16;
            for (String l : lineas) {
                c.drawText(l, MARGEN + 10, yy, p);
                yy += 13;
            }
            y += h + 10;
        }
    }

    private static List<String> partir(String texto, Paint p, float ancho) {
        List<String> lineas = new ArrayList<>();
        StringBuilder actual = new StringBuilder();
        for (String palabra : texto.split("\\s+")) {
            String prueba = actual.length() == 0 ? palabra : actual + " " + palabra;
            if (p.measureText(prueba) <= ancho || actual.length() == 0) {
                actual.setLength(0);
                actual.append(prueba);
            } else {
                lineas.add(actual.toString());
                actual.setLength(0);
                actual.append(palabra);
            }
        }
        if (actual.length() > 0) lineas.add(actual.toString());
        return lineas;
    }

    private static String recortar(String s, Paint p, float ancho) {
        if (s == null) return "";
        if (p.measureText(s) <= ancho) return s;
        int n = p.breakText(s, true, Math.max(0, ancho - p.measureText("…")), null);
        return s.substring(0, Math.max(1, n)).trim() + "…";
    }

    private static Paint texto(float tam, boolean negrita, int color) {
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        p.setTextSize(tam);
        p.setColor(color);
        p.setTypeface(Typeface.create(Typeface.SANS_SERIF, negrita ? Typeface.BOLD : Typeface.NORMAL));
        return p;
    }

    private static Bitmap logo(Context ctx) {
        try (InputStream entrada = ctx.getAssets().open("logo-multivisa.png")) {
            Bitmap original = BitmapFactory.decodeStream(entrada);
            if (original == null) return null;
            return Bitmap.createScaledBitmap(original, 220, 220 * original.getHeight() / original.getWidth(), true);
        } catch (Exception e) {
            return null;
        }
    }
}
