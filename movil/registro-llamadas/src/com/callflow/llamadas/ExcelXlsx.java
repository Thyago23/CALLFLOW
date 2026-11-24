package com.callflow.llamadas;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.HashSet;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * Libro de Excel (.xlsx) con formato, sin librerías: el .xlsx es un ZIP de XML.
 *
 * Entrada: {hojas: [{nombre, titulo, subtitulo, cols: [{t, w, k}], filas: [[...]], alertas: [[fila, col]], total: [...]}]}
 * k = tipo de la columna: "t" texto, "n" número entero, "f" fecha (AAAA-MM-DD), "h" hora (HH:MM), "d" duración (segundos).
 * Una celda vacía ("" o null) queda en blanco pero con borde. «alertas» marca celdas en rojo; «total» es una fila final en negrita.
 */
final class ExcelXlsx {
    private ExcelXlsx() {
    }

    private static final String TIPOS = "tnfhd";
    private static final int FILA_ENCABEZADO = 4;  // fila 1 título, 2 subtítulo, 3 en blanco, 4 encabezado
    private static final LocalDate EPOCA = LocalDate.of(1899, 12, 30);

    static byte[] crear(JSONObject libro) throws Exception {
        JSONArray hojas = libro.getJSONArray("hojas");
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(bytes)) {
            StringBuilder tipos = new StringBuilder("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                    + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
                    + "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
                    + "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
                    + "<Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>"
                    + "<Override PartName=\"/xl/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml\"/>");
            StringBuilder libroXml = new StringBuilder("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                    + "<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" "
                    + "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets>");
            StringBuilder nombres = new StringBuilder();
            StringBuilder rels = new StringBuilder("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                    + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">");
            for (int i = 0; i < hojas.length(); i++) {
                JSONObject h = hojas.getJSONObject(i);
                int n = i + 1;
                tipos.append("<Override PartName=\"/xl/worksheets/sheet").append(n)
                        .append(".xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>");
                libroXml.append("<sheet name=\"").append(xml(nombreHoja(h.optString("nombre", "Hoja " + n))))
                        .append("\" sheetId=\"").append(n).append("\" r:id=\"rId").append(n).append("\"/>");
                rels.append("<Relationship Id=\"rId").append(n).append("\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet")
                        .append(n).append(".xml\"/>");
                int cols = h.getJSONArray("cols").length(), filas = h.getJSONArray("filas").length();
                if (filas > 0) {
                    nombres.append("<definedName name=\"_xlnm._FilterDatabase\" localSheetId=\"").append(i).append("\" hidden=\"1\">'")
                            .append(xml(nombreHoja(h.optString("nombre", "Hoja " + n)).replace("'", "''"))).append("'!$A$").append(FILA_ENCABEZADO)
                            .append(":$").append(columna(cols - 1)).append("$").append(FILA_ENCABEZADO + filas).append("</definedName>");
                }
                poner(zip, "xl/worksheets/sheet" + n + ".xml", hoja(h));
            }
            libroXml.append("</sheets>");
            if (nombres.length() > 0) libroXml.append("<definedNames>").append(nombres).append("</definedNames>");
            libroXml.append("</workbook>");
            rels.append("<Relationship Id=\"rId").append(hojas.length() + 1)
                    .append("\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/></Relationships>");
            tipos.append("</Types>");

            poner(zip, "[Content_Types].xml", tipos.toString());
            poner(zip, "_rels/.rels", "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                    + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
                    + "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/>"
                    + "</Relationships>");
            poner(zip, "xl/workbook.xml", libroXml.toString());
            poner(zip, "xl/_rels/workbook.xml.rels", rels.toString());
            poner(zip, "xl/styles.xml", ESTILOS);
        }
        return bytes.toByteArray();
    }

    // Estilos (cellXfs): 0 normal, 1 título, 2 subtítulo, 3 encabezado; desde 4: tipo × variante (normal, cebra, alerta, total).
    private static int estilo(char tipo, int variante) {
        return 4 + TIPOS.indexOf(tipo) * 4 + variante;
    }

    private static final String ESTILOS;

    static {
        StringBuilder xf = new StringBuilder();
        xf.append("<xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/>")
                .append("<xf numFmtId=\"0\" fontId=\"2\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/>")
                .append("<xf numFmtId=\"0\" fontId=\"3\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/>")
                .append("<xf numFmtId=\"0\" fontId=\"1\" fillId=\"2\" borderId=\"1\" xfId=\"0\" applyFont=\"1\" applyFill=\"1\" applyBorder=\"1\" applyAlignment=\"1\">"
                        + "<alignment horizontal=\"center\" vertical=\"center\" wrapText=\"1\"/></xf>");
        int[] formatos = {0, 1, 164, 166, 165};                 // t, n, f, h, d
        String[] alineado = {"left", "right", "center", "center", "right"};
        int[][] variantes = {{0, 0}, {0, 3}, {5, 4}, {4, 5}};  // {fuente, relleno}: normal, cebra, alerta, total
        for (int t = 0; t < formatos.length; t++) {
            for (int[] v : variantes) {
                xf.append("<xf numFmtId=\"").append(formatos[t]).append("\" fontId=\"").append(v[0]).append("\" fillId=\"").append(v[1])
                        .append("\" borderId=\"1\" xfId=\"0\" applyNumberFormat=\"1\" applyFont=\"1\" applyFill=\"1\" applyBorder=\"1\" applyAlignment=\"1\">")
                        .append("<alignment horizontal=\"").append(alineado[t]).append("\" vertical=\"center\"")
                        .append(t == 1 || t == 4 || t == 0 ? " indent=\"1\"" : "").append("/></xf>");
            }
        }
        ESTILOS = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                + "<styleSheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
                + "<numFmts count=\"3\"><numFmt numFmtId=\"164\" formatCode=\"dd/mm/yyyy\"/><numFmt numFmtId=\"165\" formatCode=\"[h]:mm:ss\"/>"
                + "<numFmt numFmtId=\"166\" formatCode=\"hh:mm\"/></numFmts>"
                + "<fonts count=\"6\">"
                + "<font><sz val=\"11\"/><color rgb=\"FF1F2D3D\"/><name val=\"Calibri\"/><family val=\"2\"/></font>"
                + "<font><b/><sz val=\"11\"/><color rgb=\"FFFFFFFF\"/><name val=\"Calibri\"/><family val=\"2\"/></font>"
                + "<font><b/><sz val=\"16\"/><color rgb=\"FF0A2A4A\"/><name val=\"Calibri\"/><family val=\"2\"/></font>"
                + "<font><sz val=\"10\"/><color rgb=\"FF5B6F84\"/><name val=\"Calibri\"/><family val=\"2\"/></font>"
                + "<font><b/><sz val=\"11\"/><color rgb=\"FF0A2A4A\"/><name val=\"Calibri\"/><family val=\"2\"/></font>"
                + "<font><b/><sz val=\"11\"/><color rgb=\"FFC62828\"/><name val=\"Calibri\"/><family val=\"2\"/></font>"
                + "</fonts>"
                + "<fills count=\"6\"><fill><patternFill patternType=\"none\"/></fill><fill><patternFill patternType=\"gray125\"/></fill>"
                + "<fill><patternFill patternType=\"solid\"><fgColor rgb=\"FF0A2A4A\"/><bgColor indexed=\"64\"/></patternFill></fill>"
                + "<fill><patternFill patternType=\"solid\"><fgColor rgb=\"FFF6F9FC\"/><bgColor indexed=\"64\"/></patternFill></fill>"
                + "<fill><patternFill patternType=\"solid\"><fgColor rgb=\"FFFDECEC\"/><bgColor indexed=\"64\"/></patternFill></fill>"
                + "<fill><patternFill patternType=\"solid\"><fgColor rgb=\"FFE8F2FA\"/><bgColor indexed=\"64\"/></patternFill></fill></fills>"
                + "<borders count=\"2\"><border><left/><right/><top/><bottom/><diagonal/></border>"
                + "<border><left style=\"thin\"><color rgb=\"FFCFDCEB\"/></left><right style=\"thin\"><color rgb=\"FFCFDCEB\"/></right>"
                + "<top style=\"thin\"><color rgb=\"FFCFDCEB\"/></top><bottom style=\"thin\"><color rgb=\"FFCFDCEB\"/></bottom><diagonal/></border></borders>"
                + "<cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs>"
                + "<cellXfs count=\"" + (4 + formatos.length * 4) + "\">" + xf + "</cellXfs>"
                + "<cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles>"
                + "</styleSheet>";
    }

    private static String hoja(JSONObject h) throws Exception {
        JSONArray cols = h.getJSONArray("cols"), filas = h.getJSONArray("filas");
        JSONArray total = h.optJSONArray("total");
        int nc = cols.length();
        char[] tipo = new char[nc];
        for (int c = 0; c < nc; c++) {
            String k = cols.getJSONObject(c).optString("k", "t");
            tipo[c] = k.length() == 1 && TIPOS.indexOf(k.charAt(0)) >= 0 ? k.charAt(0) : 't';
        }
        Set<Long> alertas = new HashSet<>();
        JSONArray a = h.optJSONArray("alertas");
        if (a != null) for (int i = 0; i < a.length(); i++) alertas.add(((long) a.getJSONArray(i).getInt(0) << 16) | a.getJSONArray(i).getInt(1));

        String ultima = columna(nc - 1);
        int filaFinal = FILA_ENCABEZADO + filas.length() + (total != null ? 1 : 0);
        StringBuilder s = new StringBuilder("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                + "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" "
                + "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
                + "<sheetPr><pageSetUpPr fitToPage=\"1\"/></sheetPr>");
        s.append("<dimension ref=\"A1:").append(ultima).append(filaFinal).append("\"/>");
        s.append("<sheetViews><sheetView workbookViewId=\"0\" showGridLines=\"0\">")
                .append("<pane ySplit=\"").append(FILA_ENCABEZADO).append("\" topLeftCell=\"A").append(FILA_ENCABEZADO + 1)
                .append("\" activePane=\"bottomLeft\" state=\"frozen\"/><selection pane=\"bottomLeft\" activeCell=\"A")
                .append(FILA_ENCABEZADO + 1).append("\" sqref=\"A").append(FILA_ENCABEZADO + 1).append("\"/></sheetView></sheetViews>");
        s.append("<sheetFormatPr defaultRowHeight=\"18\"/><cols>");
        for (int c = 0; c < nc; c++) {
            s.append("<col min=\"").append(c + 1).append("\" max=\"").append(c + 1).append("\" width=\"")
                    .append(cols.getJSONObject(c).optDouble("w", 14)).append("\" customWidth=\"1\"/>");
        }
        s.append("</cols><sheetData>");
        s.append("<row r=\"1\" ht=\"26\" customHeight=\"1\">").append(texto("A1", h.optString("titulo"), 1)).append("</row>");
        s.append("<row r=\"2\">").append(texto("A2", h.optString("subtitulo"), 2)).append("</row>");
        s.append("<row r=\"").append(FILA_ENCABEZADO).append("\" ht=\"30\" customHeight=\"1\">");
        for (int c = 0; c < nc; c++) s.append(texto(columna(c) + FILA_ENCABEZADO, cols.getJSONObject(c).optString("t"), 3));
        s.append("</row>");
        for (int f = 0; f < filas.length(); f++) {
            int r = FILA_ENCABEZADO + 1 + f;
            JSONArray fila = filas.getJSONArray(f);
            s.append("<row r=\"").append(r).append("\">");
            for (int c = 0; c < nc; c++) {
                int variante = alertas.contains(((long) f << 16) | c) ? 2 : (f % 2 == 1 ? 1 : 0);
                s.append(celda(columna(c) + r, fila.opt(c), tipo[c], variante));
            }
            s.append("</row>");
        }
        if (total != null) {
            s.append("<row r=\"").append(filaFinal).append("\">");
            for (int c = 0; c < nc; c++) s.append(celda(columna(c) + filaFinal, total.opt(c), tipo[c], 3));
            s.append("</row>");
        }
        s.append("</sheetData>");
        if (filas.length() > 0) {
            s.append("<autoFilter ref=\"A").append(FILA_ENCABEZADO).append(":").append(ultima).append(FILA_ENCABEZADO + filas.length()).append("\"/>");
        }
        s.append("<mergeCells count=\"2\"><mergeCell ref=\"A1:").append(ultima).append("1\"/><mergeCell ref=\"A2:").append(ultima).append("2\"/></mergeCells>");
        s.append("<pageMargins left=\"0.4\" right=\"0.4\" top=\"0.5\" bottom=\"0.5\" header=\"0.3\" footer=\"0.3\"/>")
                .append("<pageSetup paperSize=\"9\" orientation=\"landscape\" fitToWidth=\"1\" fitToHeight=\"0\"/>");
        s.append("</worksheet>");
        return s.toString();
    }

    private static String celda(String ref, Object v, char tipo, int variante) {
        int st = estilo(tipo, variante);
        if (v == null || v == JSONObject.NULL || "".equals(v)) return "<c r=\"" + ref + "\" s=\"" + st + "\"/>";
        String txt = String.valueOf(v);
        try {
            switch (tipo) {
                case 'n':
                    return numero(ref, Double.parseDouble(txt), st);
                case 'd':
                    return numero(ref, Double.parseDouble(txt) / 86400.0, st);
                case 'f':
                    return numero(ref, ChronoUnit.DAYS.between(EPOCA, LocalDate.parse(txt)), st);
                case 'h':
                    return numero(ref, (Integer.parseInt(txt.substring(0, 2)) * 60 + Integer.parseInt(txt.substring(3, 5))) / 1440.0, st);
                default:
                    return texto(ref, txt, st);
            }
        } catch (RuntimeException e) {
            // Un valor que no es del tipo de la columna (p. ej. «—») se deja como texto, con el mismo estilo.
            return texto(ref, txt, st);
        }
    }

    private static String numero(String ref, double v, int st) {
        String n = v == Math.rint(v) && Math.abs(v) < 1e15 ? String.valueOf((long) v) : String.valueOf(v);
        return "<c r=\"" + ref + "\" s=\"" + st + "\"><v>" + n + "</v></c>";
    }

    private static String texto(String ref, String v, int st) {
        return "<c r=\"" + ref + "\" s=\"" + st + "\" t=\"inlineStr\"><is><t xml:space=\"preserve\">" + xml(v) + "</t></is></c>";
    }

    private static String columna(int c) {
        StringBuilder s = new StringBuilder();
        for (c++; c > 0; c = (c - 1) / 26) s.insert(0, (char) ('A' + (c - 1) % 26));
        return s.toString();
    }

    /** Excel no admite en el nombre de una hoja  \ / ? * [ ] :  ni más de 31 caracteres. */
    private static String nombreHoja(String n) {
        n = n.replaceAll("[\\\\/?*\\[\\]:]", " ").trim();
        if (n.isEmpty()) n = "Hoja";
        return n.length() > 31 ? n.substring(0, 31) : n;
    }

    private static String xml(String s) {
        StringBuilder b = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            switch (ch) {
                case '&': b.append("&amp;"); break;
                case '<': b.append("&lt;"); break;
                case '>': b.append("&gt;"); break;
                case '"': b.append("&quot;"); break;
                default:
                    // Caracteres de control no válidos en XML 1.0 (salvo tab, salto de línea y retorno).
                    if (ch < 0x20 && ch != '\t' && ch != '\n' && ch != '\r') continue;
                    b.append(ch);
            }
        }
        return b.toString();
    }

    private static void poner(ZipOutputStream zip, String nombre, String contenido) throws Exception {
        zip.putNextEntry(new ZipEntry(nombre));
        zip.write(contenido.getBytes(StandardCharsets.UTF_8));
        zip.closeEntry();
    }
}
