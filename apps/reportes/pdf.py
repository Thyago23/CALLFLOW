"""
Exportación a PDF con el formato institucional (SCRUM-63): logo, título, periodo, filtros,
tabla con encabezado repetido en cada hoja, totales, notas y pie con página y autor.
"""
from django.contrib.staticfiles import finders
from django.utils import timezone
from fpdf import FPDF

from apps.documentos.contratos import _latin1

TINTA, GRIS, SUAVE, LINEA = (14, 50, 92), (95, 105, 120), (229, 235, 243), (214, 220, 229)


class DocumentoPDF(FPDF):
    def __init__(self, titulo: str, periodo: str, filtros: str, usuario, horizontal=False):
        super().__init__(orientation="L" if horizontal else "P", format="A4")
        self.titulo, self.periodo, self.filtros = titulo, periodo, filtros
        self.autor = (usuario.get_full_name() or usuario.username) if usuario else ""
        self.generado = timezone.localtime()
        self.set_title(_latin1(titulo))
        self.set_creator("CALLFLOW")
        self.set_margins(15, 15, 15)
        self.set_auto_page_break(True, 16)
        self.alias_nb_pages()
        self.add_page()

    @property
    def ancho(self) -> float:
        return self.w - self.l_margin - self.r_margin

    def header(self):
        self.set_text_color(*TINTA)
        self.set_font("Helvetica", "B", 15)
        self.cell(self.ancho - 22, 8, _latin1(self.titulo.upper()), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*GRIS)
        self.set_font("Helvetica", size=9.5)
        self.cell(self.ancho - 22, 5, _latin1(self.periodo), new_x="LMARGIN", new_y="NEXT")
        if self.filtros:
            self.set_font("Helvetica", size=8.5)
            self.cell(self.ancho - 22, 4.5, _latin1(self.filtros), new_x="LMARGIN", new_y="NEXT")
        logo = finders.find("img/logo-multivisa.png")
        if logo:
            self.image(logo, x=self.w - self.r_margin - 15, y=self.t_margin - 3, h=15)
        self.set_y(max(self.get_y(), self.t_margin + 13) + 1.5)
        self.set_draw_color(*TINTA)
        self.set_line_width(0.5)
        self.line(self.l_margin, self.get_y(), self.l_margin + self.ancho, self.get_y())
        self.set_line_width(0.2)
        self.ln(4)

    def footer(self):
        self.set_y(-11)
        self.set_font("Helvetica", "I", 7.5)
        self.set_text_color(*GRIS)
        self.cell(self.ancho / 2, 4, _latin1(f"Generado por CALLFLOW el {self.generado:%d/%m/%Y %H:%M}"
                                             + (f" por {self.autor}" if self.autor else "")))
        self.cell(self.ancho / 2, 4, _latin1(f"Página {self.page_no()} de {{nb}}"), align="R")

    # ------------------------------------------------------------------ piezas
    def subtitulo(self, texto: str):
        if self.get_y() > self.h - 40:
            self.add_page()
        self.set_text_color(*TINTA)
        self.set_font("Helvetica", "B", 10.5)
        self.cell(self.ancho, 7, _latin1(texto), new_x="LMARGIN", new_y="NEXT")

    def notas(self, notas):
        if not notas:
            return
        self.ln(2)
        self.set_text_color(*GRIS)
        self.set_font("Helvetica", size=8.5)
        for nota in notas:
            self.multi_cell(self.ancho, 4.3, _latin1(nota), new_x="LMARGIN", new_y="NEXT")

    def _ajustar(self, texto: str, ancho: float) -> str:
        texto = _latin1(texto)
        if self.get_string_width(texto) <= ancho - 2:
            return texto
        while texto and self.get_string_width(texto + "...") > ancho - 2:
            texto = texto[:-1]
        return texto + "..."

    def _fila(self, celdas, anchos, alto, negrita=False, encabezado=False):
        if encabezado:
            self.set_fill_color(*SUAVE)
            self.set_text_color(*TINTA)
            self.set_font("Helvetica", "B", 7.5)
        else:
            self.set_text_color(30)
            self.set_font("Helvetica", "B" if negrita else "", 8.5)
        for (texto, derecha), ancho in zip(celdas, anchos):
            self.cell(ancho, alto, self._ajustar(texto, ancho), border="B", align="R" if derecha else "L", fill=encabezado)
        self.ln(alto)

    def tabla(self, tabla):
        """Dibuja una catalogo.Tabla. El encabezado se repite al cambiar de hoja."""
        total = sum(c.peso for c in tabla.columnas)
        anchos = [self.ancho * c.peso / total for c in tabla.columnas]
        encabezado = [(c.titulo.upper(), c.numerica) for c in tabla.columnas]
        self.set_draw_color(*LINEA)
        self._fila(encabezado, anchos, 7, encabezado=True)
        filas = tabla.filas_formateadas(vacio="-")
        if not filas:
            self.set_text_color(*GRIS)
            self.set_font("Helvetica", "I", 9)
            self.cell(self.ancho, 9, _latin1(tabla.vacio), new_x="LMARGIN", new_y="NEXT")
        for celdas in filas:
            if self.get_y() + 6.2 > self.page_break_trigger:
                self.add_page()
                self.set_draw_color(*LINEA)
                self._fila(encabezado, anchos, 7, encabezado=True)
            self._fila(celdas, anchos, 6.2)
        if tabla.totales:
            y = self.get_y()
            self.set_draw_color(*TINTA)
            self.set_line_width(0.4)
            self.line(self.l_margin, y, self.l_margin + self.ancho, y)
            self.set_line_width(0.2)
            self._fila(tabla.totales_formateados(vacio=""), anchos, 7, negrita=True)
        self.notas(tabla.notas)

    def salida(self) -> bytes:
        return bytes(self.output())


def tabla_pdf(titulo: str, tabla, filtros: str, usuario) -> bytes:
    pdf = DocumentoPDF(titulo, tabla.periodo, filtros, usuario, horizontal=tabla.horizontal)
    pdf.tabla(tabla)
    return pdf.salida()


# --------------------------------------------------------------------------- estadísticas (SCRUM-69)
def _indicadores(pdf, indicadores):
    """Fila de recuadros: [(título, valor, detalle)]."""
    separacion = 4
    ancho = (pdf.ancho - separacion * (len(indicadores) - 1)) / len(indicadores)
    y = pdf.get_y()
    for i, (titulo, valor, detalle) in enumerate(indicadores):
        x = pdf.l_margin + i * (ancho + separacion)
        pdf.set_draw_color(*LINEA)
        pdf.rect(x, y, ancho, 20)
        pdf.set_xy(x + 3, y + 2.5)
        pdf.set_text_color(*GRIS)
        pdf.set_font("Helvetica", size=8)
        pdf.cell(ancho - 6, 4, _latin1(titulo))
        pdf.set_xy(x + 3, y + 7)
        pdf.set_text_color(*TINTA)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(ancho - 6, 7, _latin1(valor))
        pdf.set_xy(x + 3, y + 14.5)
        pdf.set_text_color(*GRIS)
        pdf.set_font("Helvetica", size=7.5)
        pdf.cell(ancho - 6, 4, _latin1(detalle))
    pdf.set_y(y + 25)


def _grafica_lineas(pdf, etiquetas, reales, proyectados, alto=62):
    """La misma gráfica mensual de la pantalla: real continua, proyección punteada."""
    from .formato import compacto
    from .graficas import tope

    if pdf.get_y() + alto + 8 > pdf.page_break_trigger:
        pdf.add_page()
    valores = [float(v) for v in [*reales, *proyectados] if v is not None]
    maximo = tope(max(valores, default=0))
    x0, y0 = pdf.l_margin + 14, pdf.get_y() + 2
    ancho, alto_plot = pdf.ancho - 16, alto - 10
    n = len(etiquetas)
    xs = [x0 + ancho * i / max(n - 1, 1) for i in range(n)]

    def y(v):
        return y0 + alto_plot * (1 - float(v) / maximo)

    pdf.set_font("Helvetica", size=6.5)
    pdf.set_text_color(*GRIS)
    pdf.set_line_width(0.2)
    pdf.set_draw_color(*LINEA)
    for i in range(5):
        yy = y0 + alto_plot * (1 - i / 4)
        pdf.line(x0, yy, x0 + ancho, yy)
        pdf.set_xy(pdf.l_margin, yy - 2)
        pdf.cell(12, 4, _latin1(compacto(maximo * i / 4)), align="R")
    for etiqueta, x in zip(etiquetas, xs):
        pdf.set_xy(x - 8, y0 + alto_plot + 1.5)
        pdf.cell(16, 4, _latin1(etiqueta), align="C")

    def trazo(puntos, punteado):
        pdf.set_draw_color(*TINTA)
        pdf.set_line_width(0.6)
        if punteado:
            pdf.set_dash_pattern(dash=1.6, gap=1.2)
        for (xa, ya), (xb, yb) in zip(puntos, puntos[1:]):
            pdf.line(xa, ya, xb, yb)
        pdf.set_dash_pattern()
        for xp, yp in puntos:
            pdf.set_fill_color(*((255, 255, 255) if punteado else TINTA))
            pdf.ellipse(xp - 1.1, yp - 1.1, 2.2, 2.2, style="DF")

    trazo([(xs[i], y(v)) for i, v in enumerate(reales) if v is not None], False)
    trazo([(xs[i], y(v)) for i, v in enumerate(proyectados) if v is not None], True)
    pdf.set_line_width(0.2)
    pdf.set_y(y0 + alto + 1)
    pdf.set_font("Helvetica", size=7.5)
    pdf.set_text_color(*GRIS)
    pdf.cell(pdf.ancho, 4, _latin1("Línea continua: ventas reales · Línea punteada: proyección"), align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)


def estadisticas_pdf(est, grafica, tablas, filtros: str, usuario) -> bytes:
    """`grafica` = (etiquetas, reales, proyectados); `tablas` = [(subtítulo, Tabla)]."""
    from apps.nomina.calculo import nombre_mes

    from .formato import dinero, porcentaje

    pdf = DocumentoPDF("Estadísticas y proyección de ventas", f"Periodo: {nombre_mes(est.anio, est.mes)}", filtros, usuario)
    a = est.actual
    variacion = f"{est.variacion:+.0f} % vs. {est.anterior.etiqueta}" if est.variacion is not None else "Sin mes anterior"
    _indicadores(pdf, [
        ("Total vendido", dinero(a.total), variacion),
        ("Ventas cerradas", str(a.cerradas), f"de {a.enviados} DAMI enviados"),
        ("Tasa de cierre", porcentaje(a.tasa) if a.tasa is not None else "-", f"{a.gestionadas} gestionados"),
        ("Ticket promedio", dinero(a.ticket) if a.ticket is not None else "-", "por venta cerrada"),
    ])
    pdf.subtitulo("Ventas por mes")
    _grafica_lineas(pdf, *grafica)
    for subtitulo, tabla in tablas:
        pdf.subtitulo(subtitulo)
        pdf.tabla(tabla)
        pdf.ln(3)
    return pdf.salida()
