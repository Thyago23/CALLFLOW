package com.callflow.llamadas;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.pdf.PdfDocument;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.text.Collator;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Arma el PDF del registro de llamadas (A4 vertical) y lo guarda en Descargas/CALLFLOW.
 * Contenido: cabecera, tabla de llamadas y resumen de llamadas por asesor, todo paginado.
 */
final class GeneradorPdf {
    private static final int ANCHO = 595, ALTO = 842, MARGEN = 40;  // A4 en puntos
    private static final int FILA = 20, CABECERA_TABLA = 24, PIE = 30;
    // Colores de Multivisa / CALLFLOW.
    private static final int AZUL = Color.parseColor("#0062a8"), AZUL_SUAVE = Color.parseColor("#e8f2fa");
    private static final int NOCHE = Color.parseColor("#0a2a4a"), ROSA = Color.parseColor("#ff0066");
    private static final int GRIS = Color.parseColor("#5b6f84"), LINEA = Color.parseColor("#cfdceb");
    static final String HORA_INICIO = "09:00", HORA_FIN = "18:00";  // horario de llamadas

    // Tabla de llamadas: N.°, Fecha, Día, Hora, Asesor, Número (x de inicio de cada columna).
    private static final int[] COL = {MARGEN, MARGEN + 32, MARGEN + 104, MARGEN + 172, MARGEN + 218, MARGEN + 380};
    private static final String[] TITULOS = {"N.°", "Fecha", "Día", "Hora", "Asesor", "Número de teléfono"};
    // Resumen por asesor: Asesor, Llamadas, Primera, Última.
    private static final int[] COL_RES = {MARGEN, MARGEN + 230, MARGEN + 310, MARGEN + 415};
    private static final String[] TITULOS_RES = {"Asesor", "Llamadas", "Primera", "Última"};
    private static final String[] DIAS = {"Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"};

    static final class Resultado {
        final Uri uri;
        final String nombre;

        Resultado(Uri uri, String nombre) {
            this.uri = uri;
            this.nombre = nombre;
        }
    }

    private static final class Llamada {
        String fecha, hora, asesor, numero;  // fecha AAAA-MM-DD, hora HH:MM
    }

    private GeneradorPdf() {
    }

    static Resultado guardar(Context ctx, String json, String usuario) throws Exception {
        List<Llamada> llamadas = leer(json);
        if (llamadas.isEmpty()) throw new IllegalArgumentException("No hay llamadas para guardar.");
        llamadas.sort((a, b) -> (a.fecha + a.hora).compareTo(b.fecha + b.hora));

        Date ahora = new Date();
        String nombre = "Llamadas " + usuario + " " + new SimpleDateFormat("yyyy-MM-dd HHmm", Locale.US).format(ahora) + ".pdf";
        PdfDocument pdf = new Dibujo(usuario, logo(ctx)).dibujar(llamadas, ahora);

        ContentResolver cr = ctx.getContentResolver();
        ContentValues v = new ContentValues();
        v.put(MediaStore.Downloads.DISPLAY_NAME, nombre);
        v.put(MediaStore.Downloads.MIME_TYPE, "application/pdf");
        v.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/CALLFLOW");
        v.put(MediaStore.Downloads.IS_PENDING, 1);
        Uri uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
        if (uri == null) throw new IllegalStateException("No se pudo crear el archivo en Descargas.");
        try (OutputStream salida = cr.openOutputStream(uri)) {
            if (salida == null) throw new IllegalStateException("No se pudo escribir el archivo.");
            pdf.writeTo(salida);
        } catch (Exception e) {
            cr.delete(uri, null, null);  // no deja un PDF a medias
            throw e;
        } finally {
            pdf.close();
        }
        v.clear();
        v.put(MediaStore.Downloads.IS_PENDING, 0);
        cr.update(uri, v, null, null);
        return new Resultado(uri, nombre);
    }

    private static List<Llamada> leer(String json) throws Exception {
        JSONArray arr = new JSONArray(json);
        List<Llamada> lista = new ArrayList<>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.getJSONObject(i);
            Llamada l = new Llamada();
            l.fecha = o.optString("fecha");
            l.hora = o.optString("hora");
            l.asesor = o.optString("asesor").trim();
            l.numero = o.optString("numero");
            if (l.asesor.isEmpty()) l.asesor = "Sin asesor";
            if (!l.fecha.matches("\\d{4}-\\d{2}-\\d{2}") || !l.hora.matches("\\d{2}:\\d{2}") || l.numero.isEmpty()) {
                throw new IllegalArgumentException("La llamada " + (i + 1) + " tiene datos incompletos.");
            }
            if (l.hora.compareTo(HORA_INICIO) < 0 || l.hora.compareTo(HORA_FIN) > 0) {
                throw new IllegalArgumentException("La llamada de las " + l.hora + " (" + l.numero + ") está fuera del horario de 9:00 a 18:00: quítela o corríjala.");
            }
            lista.add(l);
        }
        return lista;
    }

    /** Dibuja página por página con un cursor vertical; abre una página nueva cuando no cabe lo siguiente. */
    private static final class Dibujo {
        final Paint titulo = texto(18, true, NOCHE), subtitulo = texto(12, true, NOCHE), normal = texto(10, false, NOCHE);
        final Paint suave = texto(9, false, GRIS), negrita = texto(10, true, NOCHE), cabecera = texto(9, true, AZUL);
        final Paint relleno = new Paint(), linea = new Paint(), rosa = new Paint();
        final String usuario;
        final Bitmap logo;
        final List<Pagina> paginas = new ArrayList<>();
        PdfDocument pdf;
        Canvas c;
        int y;

        /** El pie con «Página x de y» se dibuja al final, cuando ya se sabe el total. */
        private static final class Pagina {
            final PdfDocument.Page pagina;

            Pagina(PdfDocument.Page pagina) {
                this.pagina = pagina;
            }
        }

        Dibujo(String usuario, Bitmap logo) {
            this.usuario = usuario;
            this.logo = logo;
            rosa.setColor(ROSA);
            relleno.setColor(AZUL_SUAVE);
            linea.setColor(LINEA);
            linea.setStrokeWidth(0.7f);
        }

        PdfDocument dibujar(List<Llamada> llamadas, Date ahora) {
            pdf = new PdfDocument();
            // Con PdfDocument no se puede volver a una página ya cerrada: se cuenta el total de páginas antes,
            // simulando el mismo recorrido, para poder escribir «Página x de y» en cada una.
            Map<String, int[]> porAsesor = resumen(llamadas);
            int total = contarPaginas(llamadas.size(), porAsesor.size());

            nuevaPagina(total);
            c.drawText("Registro de llamadas", MARGEN, MARGEN + 18, titulo);
            c.drawRect(MARGEN, MARGEN + 26, MARGEN + 48, MARGEN + 29, rosa);  // línea rosa de Multivisa bajo el título
            if (logo != null) {
                int lado = 72;  // logo de Multivisa arriba a la derecha
                Paint suavizado = new Paint(Paint.FILTER_BITMAP_FLAG | Paint.ANTI_ALIAS_FLAG);
                c.drawBitmap(logo, null, new android.graphics.Rect(ANCHO - MARGEN - lado, MARGEN - 8, ANCHO - MARGEN, MARGEN - 8 + lado), suavizado);
            } else {
                c.drawText("MULTIVISA", ANCHO - MARGEN - cabecera.measureText("MULTIVISA"), MARGEN + 18, cabecera);
            }
            String desde = fechaCorta(llamadas.get(0).fecha), hasta = fechaCorta(llamadas.get(llamadas.size() - 1).fecha);
            c.drawText("Usuario: " + usuario, MARGEN, MARGEN + 44, negrita);
            c.drawText("Periodo: " + (desde.equals(hasta) ? desde : desde + " al " + hasta), MARGEN, MARGEN + 60, normal);
            c.drawText("Total: " + llamadas.size() + (llamadas.size() == 1 ? " llamada" : " llamadas") + " de "
                    + porAsesor.size() + (porAsesor.size() == 1 ? " asesor" : " asesores"), MARGEN, MARGEN + 76, normal);
            c.drawText("Generado: " + new SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.US).format(ahora) + "  ·  Horario de llamadas: 9:00 a 18:00",
                    MARGEN, MARGEN + 92, suave);
            y = MARGEN + 110;

            // Tabla de llamadas
            cabeceraTabla(COL, TITULOS);
            for (int i = 0; i < llamadas.size(); i++) {
                if (y + FILA > limite()) {
                    terminarPagina(total);
                    nuevaPagina(total);
                    y = MARGEN;
                    cabeceraTabla(COL, TITULOS);
                }
                Llamada l = llamadas.get(i);
                String[] celdas = {String.valueOf(i + 1), fechaCorta(l.fecha), dia(l.fecha), l.hora, l.asesor, l.numero};
                for (int k = 0; k < celdas.length; k++) {
                    Paint p = k == 5 ? negrita : normal;
                    c.drawText(recortar(celdas[k], p, ancho(COL, k)), COL[k] + 6, y + 14, p);
                }
                y += FILA;
                c.drawLine(MARGEN, y, ANCHO - MARGEN, y, linea);
            }

            // Resumen por asesor (título + cabecera + al menos una fila deben caber juntos)
            y += 28;
            if (y + 22 + CABECERA_TABLA + FILA > limite()) {
                terminarPagina(total);
                nuevaPagina(total);
                y = MARGEN;
            }
            c.drawText("Llamadas por asesor", MARGEN, y + 12, subtitulo);
            y += 22;
            cabeceraTabla(COL_RES, TITULOS_RES);
            for (Map.Entry<String, int[]> e : porAsesor.entrySet()) {
                if (y + FILA > limite()) {
                    terminarPagina(total);
                    nuevaPagina(total);
                    y = MARGEN;
                    cabeceraTabla(COL_RES, TITULOS_RES);
                }
                int[] r = e.getValue();
                Llamada primera = llamadas.get(r[1]), ultima = llamadas.get(r[2]);
                String[] celdas = {e.getKey(), String.valueOf(r[0]), fechaCorta(primera.fecha) + " " + primera.hora,
                        fechaCorta(ultima.fecha) + " " + ultima.hora};
                for (int k = 0; k < celdas.length; k++) {
                    Paint p = k <= 1 ? negrita : normal;
                    c.drawText(recortar(celdas[k], p, ancho(COL_RES, k)), COL_RES[k] + 6, y + 14, p);
                }
                y += FILA;
                c.drawLine(MARGEN, y, ANCHO - MARGEN, y, linea);
            }
            terminarPagina(total);
            return pdf;
        }

        /** Mismo recorrido que dibujar(), solo contando cuántas páginas salen. */
        private int contarPaginas(int filas, int asesores) {
            int paginas = 1, yy = MARGEN + 110 + CABECERA_TABLA;
            for (int i = 0; i < filas; i++) {
                if (yy + FILA > limite()) { paginas++; yy = MARGEN + CABECERA_TABLA; }
                yy += FILA;
            }
            yy += 28;
            if (yy + 22 + CABECERA_TABLA + FILA > limite()) { paginas++; yy = MARGEN; }
            yy += 22 + CABECERA_TABLA;
            for (int i = 0; i < asesores; i++) {
                if (yy + FILA > limite()) { paginas++; yy = MARGEN + CABECERA_TABLA; }
                yy += FILA;
            }
            return paginas;
        }

        private int limite() {
            return ALTO - MARGEN - PIE;
        }

        private void nuevaPagina(int total) {
            PdfDocument.Page p = pdf.startPage(new PdfDocument.PageInfo.Builder(ANCHO, ALTO, paginas.size() + 1).create());
            paginas.add(new Pagina(p));
            c = p.getCanvas();
        }

        private void terminarPagina(int total) {
            Pagina actual = paginas.get(paginas.size() - 1);
            String pie = "Página " + paginas.size() + " de " + total;
            c.drawText(pie, ANCHO - MARGEN - suave.measureText(pie), ALTO - MARGEN + 10, suave);
            c.drawText("Multivisa · Registro de llamadas · " + usuario, MARGEN, ALTO - MARGEN + 10, suave);
            pdf.finishPage(actual.pagina);
        }

        private void cabeceraTabla(int[] cols, String[] titulos) {
            c.drawRect(MARGEN, y, ANCHO - MARGEN, y + CABECERA_TABLA, relleno);
            for (int k = 0; k < titulos.length; k++) c.drawText(titulos[k], cols[k] + 6, y + 16, cabecera);
            y += CABECERA_TABLA;
        }
    }

    /** Asesor → {llamadas, índice de la primera, índice de la última}, de más a menos llamadas y luego por nombre. */
    private static Map<String, int[]> resumen(List<Llamada> llamadas) {
        Map<String, int[]> mapa = new LinkedHashMap<>();
        for (int i = 0; i < llamadas.size(); i++) {
            int[] r = mapa.get(llamadas.get(i).asesor);
            if (r == null) mapa.put(llamadas.get(i).asesor, new int[]{1, i, i});
            else { r[0]++; r[2] = i; }
        }
        Collator orden = Collator.getInstance(new Locale("es"));
        List<Map.Entry<String, int[]>> lista = new ArrayList<>(mapa.entrySet());
        lista.sort((a, b) -> a.getValue()[0] != b.getValue()[0] ? b.getValue()[0] - a.getValue()[0] : orden.compare(a.getKey(), b.getKey()));
        Map<String, int[]> ordenado = new LinkedHashMap<>();
        for (Map.Entry<String, int[]> e : lista) ordenado.put(e.getKey(), e.getValue());
        return ordenado;
    }

    /** Logo de Multivisa desde assets, reducido para no inflar el PDF; null si no se puede leer. */
    private static Bitmap logo(Context ctx) {
        try (InputStream entrada = ctx.getAssets().open("logo-multivisa.png")) {
            Bitmap original = BitmapFactory.decodeStream(entrada);
            if (original == null) return null;
            return Bitmap.createScaledBitmap(original, 240, 240 * original.getHeight() / original.getWidth(), true);
        } catch (Exception e) {
            return null;
        }
    }

    private static int ancho(int[] cols, int k) {
        return (k + 1 < cols.length ? cols[k + 1] : ANCHO - MARGEN) - cols[k] - 10;
    }

    /** Corta el texto con «…» si no cabe en la columna. */
    private static String recortar(String s, Paint p, float ancho) {
        if (p.measureText(s) <= ancho) return s;
        int n = p.breakText(s, true, ancho - p.measureText("…"), null);
        return s.substring(0, Math.max(1, n)).trim() + "…";
    }

    private static Paint texto(float tam, boolean negrita, int color) {
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        p.setTextSize(tam);
        p.setColor(color);
        p.setTypeface(Typeface.create(Typeface.SANS_SERIF, negrita ? Typeface.BOLD : Typeface.NORMAL));
        return p;
    }

    /** 2026-11-17 → 17/11/2026 */
    private static String fechaCorta(String iso) {
        return iso.substring(8, 10) + "/" + iso.substring(5, 7) + "/" + iso.substring(0, 4);
    }

    private static String dia(String iso) {
        Calendar cal = Calendar.getInstance();
        cal.set(Integer.parseInt(iso.substring(0, 4)), Integer.parseInt(iso.substring(5, 7)) - 1, Integer.parseInt(iso.substring(8, 10)));
        return DIAS[cal.get(Calendar.DAY_OF_WEEK) - 1];
    }

    /** Texto como literal JSON ("…" con comillas escapadas). */
    static String jsonTexto(String s) {
        return JSONObject.quote(s);
    }
}
