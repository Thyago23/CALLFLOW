"""
Plantilla de hoja de vida (PDF para llenar) pensada para el lector de CV del sistema (apps/personal/cv.py).

Cada dato personal es una casilla del PDF cuyo nombre es el rótulo que busca el lector («Nombres»,
«Apellidos», «Cédula», «Teléfono celular», «Correo electrónico», «Dirección domiciliaria»). La fecha de
nacimiento va en tres casillas («Fecha de nacimiento - Día / Mes / Año») que el lector une. La cédula, el
celular y la fecha usan una cajita por dígito, para que no haya dudas del formato. Las casillas de
referencias se llaman «Referencia …» y el lector las ignora, para no confundir el teléfono o la cédula de
otra persona con los del candidato.

Los textos de ayuda impresos no llevan fechas, teléfonos, cédulas ni correos completos: si alguien imprime
el formulario a PDF, el lector podría tomarlos como datos reales.

Se genera con:  python manage.py generar_plantilla_cv
"""
import io
from dataclasses import dataclass

from django.contrib.staticfiles import finders
from fpdf import FPDF
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject, NumberObject,
                           TextStringObject)

NAVY, AZUL, GRIS, LINEA, FONDO = (23, 54, 93), (0, 98, 168), (95, 105, 120), (175, 188, 205), (243, 246, 250)
AMARILLO, VERDE = (255, 248, 225), (232, 245, 237)
MM = 72 / 25.4  # puntos por milímetro
PEINE = 1 << 24  # casilla «comb»: una cajita por carácter


@dataclass
class Casilla:
    nombre: str
    pagina: int
    x: float  # mm desde la izquierda
    y: float  # mm desde arriba
    ancho: float
    alto: float
    multilinea: bool = False
    ayuda: str = ""
    maximo: int = 0  # cantidad de caracteres (cajitas) en las casillas de dígitos


class _Hoja(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", size=7.5)
        self.set_text_color(*GRIS)
        self.cell(0, 5, f"Hoja de vida · MULTIVISA · página {self.page_no()} de {{nb}}", align="C")


class _Constructor:
    IZQ, ANCHO = 15.0, 180.0
    COL_ROTULO = 47.0  # ancho de la columna de rótulos en «Datos personales»

    def __init__(self):
        self.pdf = _Hoja(format="A4")
        self.pdf.set_auto_page_break(False)
        self.pdf.set_margins(self.IZQ, 15, self.IZQ)
        self.pdf.alias_nb_pages()
        self.casillas: list[Casilla] = []

    # ------------------------------------------------------------------ utilidades de dibujo
    def pagina(self):
        self.pdf.add_page()
        self.pdf.set_y(15)

    def espacio(self, alto: float):
        if self.pdf.get_y() + alto > 297 - 18:
            self.pagina()

    def texto(self, x, y, ancho, alto, texto, *, tam=9, estilo="", color=(40, 40, 40), alinear="L"):
        p = self.pdf
        p.set_xy(x, y)
        p.set_font("Helvetica", estilo, tam)
        p.set_text_color(*color)
        p.cell(ancho, alto, texto, align=alinear)

    def recuadro(self, nombre, x, y, ancho, alto, *, ayuda="", multilinea=False, maximo=0):
        p = self.pdf
        p.set_draw_color(*LINEA)
        p.set_fill_color(255, 255, 255)
        p.set_line_width(0.3)
        p.rect(x, y, ancho, alto, "DF")
        if maximo:  # divisiones de las cajitas
            p.set_draw_color(215, 222, 232)
            for i in range(1, maximo):
                p.line(x + ancho * i / maximo, y + 1.2, x + ancho * i / maximo, y + alto - 1.2)
        self.casillas.append(Casilla(nombre, p.page_no(), x, y, ancho, alto, multilinea, ayuda, maximo))

    def ayuda(self, x, y, ancho, texto):
        """Ayuda en gris. Empieza con '»': el lector de CV sabe que esa línea no es un dato."""
        p = self.pdf
        p.set_xy(x, y)
        p.set_font("Helvetica", "I", 7.6)
        p.set_text_color(*GRIS)
        p.multi_cell(ancho, 3.4, f"» {texto}")
        return p.get_y()

    def seccion(self, numero: int, titulo: str, nota: str = "", *, contenido: float = 14):
        """Título de sección. Si su primer bloque (contenido, en mm) no cabe, empieza en la página siguiente."""
        self.espacio(11 + contenido)
        p = self.pdf
        y = p.get_y()
        p.set_fill_color(*AZUL)
        p.rect(self.IZQ, y, self.ANCHO, 8, "F")
        p.set_fill_color(255, 255, 255)
        p.ellipse(self.IZQ + 2.2, y + 1.3, 5.4, 5.4, "F")
        self.texto(self.IZQ + 2.2, y + 1.6, 5.4, 4.8, str(numero), tam=9, estilo="B", color=AZUL, alinear="C")
        self.texto(self.IZQ + 10, y + 1.6, 120, 4.8, titulo.upper(), tam=10, estilo="B", color=(255, 255, 255))
        if nota:
            self.texto(self.IZQ + 90, y + 1.8, self.ANCHO - 92, 4.5, nota, tam=7.8, color=(225, 236, 247), alinear="R")
        p.set_y(y + 11)

    # ------------------------------------------------------------------ bloques
    def encabezado(self):
        p = self.pdf
        p.set_fill_color(*NAVY)
        p.rect(0, 0, 210, 30, "F")
        logo = finders.find("img/logo-multivisa.png")
        if logo:
            p.set_fill_color(255, 255, 255)
            p.rect(15, 5, 20, 20, "F")
            p.image(logo, x=16, y=6, w=18)
        self.texto(40, 7, 150, 9, "HOJA DE VIDA", tam=20, estilo="B", color=(255, 255, 255))
        self.texto(40, 17, 150, 5, "MULTIVISA · Formulario para postular", tam=9.5, color=(220, 230, 242))
        p.set_y(36)

    def como_llenar(self):
        p = self.pdf
        y = p.get_y()
        alto = 58
        p.set_fill_color(*FONDO)
        p.set_draw_color(*LINEA)
        p.rect(self.IZQ, y, self.ANCHO, alto, "DF")
        self.texto(self.IZQ + 4, y + 3, 170, 5, "CÓMO LLENAR ESTA HOJA DE VIDA", tam=10.5, estilo="B", color=NAVY)
        pasos = [
            ("Ábrala en la computadora o el celular", "con Adobe Acrobat Reader (gratis), Microsoft Edge o Google Chrome. "
             "No la llene a mano."),
            ("Haga clic en cada recuadro blanco y escriba", "Para pasar al siguiente recuadro use la tecla Tab. "
             "La ayuda en gris debajo de cada recuadro le dice cómo escribir el dato."),
            ("Guárdela como PDF y envíela tal cual", "En el menú Archivo > Guardar (o el ícono de descarga). "
             "No la imprima, no la escanee y no le tome foto: el sistema solo lee el PDF lleno."),
            ("Los datos personales se pasan solos a su ficha", "Por eso deben ir exactamente como se pide: "
             "cédula y celular con sus 10 números, y la fecha de nacimiento en día, mes y año por separado."),
        ]
        yy = y + 10
        for i, (titulo, detalle) in enumerate(pasos, start=1):
            p.set_fill_color(*AZUL)
            p.ellipse(self.IZQ + 4, yy + 0.3, 5, 5, "F")
            self.texto(self.IZQ + 4, yy + 0.5, 5, 4.6, str(i), tam=8.5, estilo="B", color=(255, 255, 255), alinear="C")
            self.texto(self.IZQ + 12, yy, 160, 4.5, titulo, tam=9, estilo="B", color=NAVY)
            p.set_xy(self.IZQ + 12, yy + 4.4)
            p.set_font("Helvetica", size=8.3)
            p.set_text_color(55, 55, 55)
            p.multi_cell(self.ANCHO - 16, 3.7, detalle)
            yy = p.get_y() + 1.6
        p.set_y(y + alto + 5)

    def fila_dato(self, nombre, rotulo, ayuda, *, ancho_caja=None, maximo=0):
        """Rótulo a la izquierda, recuadro a la derecha y la ayuda en gris debajo del recuadro."""
        self.espacio(18)
        p = self.pdf
        y = p.get_y()
        x = self.IZQ + self.COL_ROTULO
        ancho = ancho_caja or (self.ANCHO - self.COL_ROTULO)
        self.texto(self.IZQ + 2, y + 2, self.COL_ROTULO - 2, 4.5, f"{rotulo}:", tam=9.5, estilo="B", color=NAVY)
        self.recuadro(nombre, x, y, ancho, 8.5, ayuda=ayuda, maximo=maximo)
        fin = self.ayuda(x, y + 9.3, self.ANCHO - self.COL_ROTULO, ayuda)
        p.set_y(fin + 2.8)

    def fila_fecha(self):
        """Día, mes y año en recuadros separados (el lector los une)."""
        self.espacio(24)
        p = self.pdf
        y = p.get_y()
        x = self.IZQ + self.COL_ROTULO
        self.texto(self.IZQ + 2, y + 6.5, self.COL_ROTULO - 2, 4.5, "Fecha de nacimiento:", tam=9.5, estilo="B", color=NAVY)
        partes = [("Día", 2, 14), ("Mes", 2, 14), ("Año", 4, 26)]
        xx = x
        for i, (parte, cifras, ancho) in enumerate(partes):
            self.texto(xx, y, ancho, 4, parte.upper(), tam=7.5, estilo="B", color=GRIS, alinear="C")
            self.recuadro(f"Fecha de nacimiento - {parte}", xx, y + 4.5, ancho, 8.5,
                          ayuda=f"{parte} de nacimiento con números ({cifras} cifras)", maximo=cifras)
            xx += ancho
            if i < 2:
                self.texto(xx, y + 5.5, 6, 6, "/", tam=13, color=LINEA, alinear="C")
                xx += 6
        # Ejemplo dibujado con las mismas cajitas (no es texto que el lector pueda tomar como fecha).
        ex = xx + 7
        fin_x = self.IZQ + self.ANCHO
        p.set_fill_color(*AMARILLO)
        p.set_draw_color(225, 205, 140)
        p.rect(ex, y - 0.5, fin_x - ex, 14, "DF")
        self.texto(ex + 2, y + 0.3, fin_x - ex - 4, 3.5, "EJEMPLO: si nació el 20 de abril de 1995", tam=6.8, estilo="B",
                   color=(110, 85, 20))
        # Las mismas cajitas, llenas en gris: cada cifra es un texto aparte (no se lee como una fecha).
        cx, cy, cajita = ex + 2.5, y + 5, 4.6
        for i, grupo in enumerate(("20", "04", "1995")):
            for cifra in grupo:
                p.set_fill_color(255, 255, 255)
                p.set_draw_color(*LINEA)
                p.rect(cx, cy, cajita, 6.5, "DF")
                self.texto(cx, cy + 1, cajita, 4.5, cifra, tam=9, color=(120, 120, 120), alinear="C")
                cx += cajita
            if i < 2:
                self.texto(cx, cy + 1, 3.4, 4.5, "/", tam=9, color=GRIS, alinear="C")
                cx += 3.4
        fin = self.ayuda(x, y + 14, self.ANCHO - self.COL_ROTULO,
                         "Escriba el día y el mes con dos números (agregue un cero adelante si hace falta: el día cinco es 05, "
                         "el mes de abril es 04) y el año con cuatro números.")
        p.set_y(fin + 2.8)

    def campo_arriba(self, nombre, rotulo, x, ancho, *, alto=8, multilinea=False, ayuda=""):
        """Rótulo arriba del recuadro (secciones de formación, experiencia y referencias)."""
        y = self.pdf.get_y()
        self.texto(x, y, ancho, 4.5, f"{rotulo}:", tam=8.5, estilo="B", color=NAVY)
        self.recuadro(nombre, x, y + 5, ancho, alto, ayuda=ayuda or rotulo, multilinea=multilinea)
        return y + 5 + alto

    def fila(self, campos, *, alto=8):
        self.espacio(alto + 9)
        y = self.pdf.get_y()
        total = sum(c[2] for c in campos)
        sep, x, fin = 4, self.IZQ, y
        util = self.ANCHO - sep * (len(campos) - 1)
        for nombre, rotulo, relativo in campos:
            self.pdf.set_y(y)
            ancho = util * relativo / total
            fin = max(fin, self.campo_arriba(nombre, rotulo, x, ancho, alto=alto))
            x += ancho + sep
        self.pdf.set_y(fin + 3)

    def bloque(self, nombre, rotulo, alto, ayuda=""):
        self.espacio(alto + 12)
        fin = self.campo_arriba(nombre, rotulo, self.IZQ, self.ANCHO, alto=alto, multilinea=True, ayuda=ayuda)
        if ayuda:
            fin = self.ayuda(self.IZQ, fin + 0.6, self.ANCHO, ayuda)
        self.pdf.set_y(fin + 3)

    def revision_final(self):
        self.espacio(46)
        p = self.pdf
        y = p.get_y() + 2
        puntos = [
            "Mis nombres y apellidos están completos, como en la cédula.",
            "La cédula y el celular tienen 10 números cada uno, sin espacios ni guiones.",
            "La fecha de nacimiento está en día, mes y año (por ejemplo, el mes de abril es 04).",
            "El correo está bien escrito y sin espacios.",
            "Guardé el archivo como PDF (no lo imprimí ni le tomé foto).",
        ]
        alto = 9 + len(puntos) * 5.4
        p.set_fill_color(*VERDE)
        p.set_draw_color(160, 205, 175)
        p.rect(self.IZQ, y, self.ANCHO, alto, "DF")
        self.texto(self.IZQ + 4, y + 2.5, 170, 5, "ANTES DE ENVIAR, REVISE", tam=10, estilo="B", color=(20, 90, 50))
        for i, punto in enumerate(puntos):
            yy = y + 9 + i * 5.4
            p.set_draw_color(20, 90, 50)
            p.set_fill_color(255, 255, 255)
            p.rect(self.IZQ + 5, yy + 0.4, 3.6, 3.6, "DF")
            self.texto(self.IZQ + 11, yy, 165, 4.5, punto, tam=8.8, color=(30, 60, 40))
        p.set_y(y + alto + 5)
        p.set_font("Helvetica", size=8.3)
        p.set_text_color(60, 60, 60)
        p.multi_cell(self.ANCHO, 4, "Declaro que la información de esta hoja de vida es verdadera y autorizo a MULTIVISA a "
                                     "verificarla y a usarla para el proceso de selección y, si soy contratado(a), para mi ficha de empleado.")

    # ------------------------------------------------------------------ documento
    def construir(self) -> bytes:
        self.pagina()
        self.encabezado()
        self.como_llenar()

        self.seccion(1, "Datos personales", "Estos datos se pasan solos a su ficha de empleado")
        self.fila_dato("Nombres", "Nombres", "Sus nombres completos, tal como aparecen en la cédula. Ejemplo: Jonathan Rodrigo")
        self.fila_dato("Apellidos", "Apellidos", "Sus dos apellidos, tal como aparecen en la cédula. Ejemplo: Quintana Chamba")
        self.fila_dato("Cédula", "Cédula de identidad",
                       "Los 10 números de su cédula, uno en cada cajita. Sin guion ni espacios.", ancho_caja=80, maximo=10)
        self.fila_fecha()
        self.fila_dato("Teléfono celular", "Teléfono celular",
                       "Los 10 números de su celular, uno en cada cajita. Empieza con 09. Sin espacios y sin el +593.",
                       ancho_caja=80, maximo=10)
        self.fila_dato("Correo electrónico", "Correo electrónico",
                       "Su correo personal completo (Gmail, Hotmail, Outlook...), en minúsculas y sin espacios.")
        self.fila_dato("Dirección domiciliaria", "Dirección domiciliaria",
                       "Calle principal, número de casa, calle secundaria, sector o barrio y ciudad.")

        self.seccion(2, "Postulación")
        self.fila([("Cargo al que postula", "Cargo al que postula", 3), ("Disponibilidad", "¿Desde cuándo puede empezar?", 2)])

        self.seccion(3, "Perfil profesional", contenido=34)
        self.bloque("Perfil profesional", "Cuéntenos quién es y qué sabe hacer", 20,
                    ayuda="Tres o cuatro líneas: su experiencia, sus fortalezas y por qué quiere trabajar con nosotros.")

        self.seccion(4, "Formación académica", "Empiece por la más reciente", contenido=30)
        for i in (1, 2):
            self.fila([(f"Formación {i} - Título", "Título obtenido", 3), (f"Formación {i} - Institución", "Colegio o universidad", 3),
                       (f"Formación {i} - Año", "Año en que terminó", 1.4)])

        self.seccion(5, "Experiencia laboral", "Empiece por la más reciente", contenido=36)
        for i in (1, 2, 3):
            self.fila([(f"Experiencia {i} - Empresa", "Empresa", 3), (f"Experiencia {i} - Cargo", "Cargo", 3),
                       (f"Experiencia {i} - Periodo", "Desde - hasta (mes/año)", 2)])
            self.bloque(f"Experiencia {i} - Funciones", "Funciones y logros", 12)

        self.seccion(6, "Habilidades e idiomas", contenido=28)
        self.bloque("Habilidades", "Habilidades, programas que maneja e idiomas", 16,
                    ayuda="Por ejemplo: ventas por teléfono, atención al cliente, Excel, inglés intermedio.")

        self.seccion(7, "Referencias personales", "Personas que lo conozcan (no familiares)", contenido=34)
        for i in (1, 2):
            self.fila([(f"Referencia {i} - Nombre", "Nombre completo", 3), (f"Referencia {i} - Relación", "Relación (exjefe, compañero...)", 2.2),
                       (f"Referencia {i} - Teléfono", "Teléfono", 1.8)])

        self.revision_final()
        return bytes(self.pdf.output())


def _agregar_casillas(base: bytes, casillas: list[Casilla]) -> bytes:
    """Convierte los recuadros en casillas para llenar (AcroForm) con pypdf."""
    lector = PdfReader(io.BytesIO(base))
    escritor = PdfWriter()
    escritor.append(lector)
    fuente = escritor._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    campos = ArrayObject()
    for c in casillas:
        pagina = escritor.pages[c.pagina - 1]
        alto_pagina = float(pagina.mediabox.height)
        x1, y2 = c.x * MM, alto_pagina - c.y * MM
        borde = 0 if c.maximo else 1  # las de cajitas ocupan todo el recuadro para que cada cifra caiga en su cajita
        rect = ArrayObject([FloatObject(x1 + borde), FloatObject(y2 - c.alto * MM + borde),
                            FloatObject(x1 + c.ancho * MM - borde), FloatObject(y2 - borde)])
        tamano = 9 if c.multilinea else (12 if c.maximo else 10.5)
        banderas = 4096 if c.multilinea else (PEINE if c.maximo else 0)
        widget = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject(c.nombre),
            NameObject("/TU"): TextStringObject(c.ayuda or c.nombre), NameObject("/Rect"): rect, NameObject("/F"): NumberObject(4),
            NameObject("/Ff"): NumberObject(banderas),
            NameObject("/DA"): TextStringObject(f"/Helv {tamano} Tf 0 g"), NameObject("/V"): TextStringObject(""),
            NameObject("/P"): pagina.indirect_reference,
        })
        if c.maximo:
            widget[NameObject("/MaxLen")] = NumberObject(c.maximo)
        referencia = escritor._add_object(widget)
        if "/Annots" not in pagina:
            pagina[NameObject("/Annots")] = ArrayObject()
        pagina["/Annots"].append(referencia)
        campos.append(referencia)
    escritor._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): campos, NameObject("/NeedAppearances"): BooleanObject(True),
        NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"),
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): fuente})}),
    })
    escritor.add_metadata({"/Title": "Hoja de vida - MULTIVISA", "/Creator": "CALLFLOW"})
    salida = io.BytesIO()
    escritor.write(salida)
    return salida.getvalue()


def generar() -> bytes:
    constructor = _Constructor()
    base = constructor.construir()
    return _agregar_casillas(base, constructor.casillas)
