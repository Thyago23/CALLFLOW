"""
Acta de entrega / devolución de un equipo en PDF, lista para imprimir y firmar.

Tiene los datos del colaborador y del equipo, las condiciones de la entrega (asignación o préstamo),
el estado físico y los accesorios, la cláusula de responsabilidad y dos bloques de firma.
"""
from django.contrib.staticfiles import finders
from django.utils import timezone
from fpdf import FPDF

from apps.documentos.contratos import _latin1, fecha_texto

from .models import ActaEquipo

TINTA, GRIS, SUAVE, LINEA = (14, 50, 92), (95, 105, 120), (229, 235, 243), (214, 220, 229)
EMPRESA = "MULTIVISA"

CLAUSULA_ENTREGA = (
    "El colaborador declara recibir el equipo descrito en el estado y con los accesorios indicados, para uso "
    "exclusivo de sus funciones en la empresa. Se compromete a cuidarlo, a no instalar programas ni modificar su "
    "configuración sin autorización del departamento de TI, a no prestarlo a terceros y a informar de inmediato "
    "cualquier daño, falla, pérdida o robo. {plazo}Al terminar su relación laboral, o cuando la empresa lo solicite, "
    "devolverá el equipo con sus accesorios. Los daños por mal uso o negligencia podrán serle imputados conforme "
    "al reglamento interno y la ley."
)
PLAZO_PRESTAMO = "Por tratarse de un préstamo, lo devolverá a más tardar el {fecha}. "
CLAUSULA_DEVOLUCION = (
    "El departamento de TI declara recibir el equipo descrito en el estado y con los accesorios indicados. "
    "Con la firma de esta acta queda registrada la devolución; las observaciones anotadas reflejan el estado del "
    "equipo al momento de recibirlo."
)


class _PDFActa(FPDF):
    def __init__(self, acta: ActaEquipo):
        super().__init__(format="A4")
        self.acta = acta
        self.generado = timezone.localtime()
        titulo = f"Acta de {acta.get_tipo_display().lower()} {acta.numero}"
        self.set_title(_latin1(titulo))
        self.set_creator("CALLFLOW")
        self.set_margins(20, 16, 20)
        self.set_auto_page_break(True, 18)
        self.alias_nb_pages()

    @property
    def ancho(self) -> float:
        return self.w - self.l_margin - self.r_margin

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7.5)
        self.set_text_color(*GRIS)
        self.cell(self.ancho * 0.75, 4, _latin1(f"{self.acta.numero} · Generada por CALLFLOW el {self.generado:%d/%m/%Y %H:%M}"))
        self.cell(self.ancho * 0.25, 4, _latin1(f"Página {self.page_no()} de {{nb}}"), align="R")

    # ------------------------------------------------------------------ piezas
    def encabezado(self, titulo: str):
        logo = finders.find("img/logo-multivisa.png")
        if logo:
            self.image(logo, x=self.w - self.r_margin - 18, y=self.t_margin - 4, h=18)
        self.set_text_color(*TINTA)
        self.set_font("Helvetica", "B", 15)
        self.cell(self.ancho - 24, 8, _latin1(titulo), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*GRIS)
        self.set_font("Helvetica", size=9.5)
        self.cell(self.ancho - 24, 5, _latin1(f"{EMPRESA} · Departamento de Tecnología (TI)"), new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "B", 9.5)
        self.cell(self.ancho - 24, 5, _latin1(f"N.º {self.acta.numero} · {fecha_texto(self.acta.fecha)}"), new_x="LMARGIN", new_y="NEXT")
        self.set_y(max(self.get_y(), self.t_margin + 16) + 1)
        self.set_draw_color(*TINTA)
        self.set_line_width(0.5)
        self.line(self.l_margin, self.get_y(), self.l_margin + self.ancho, self.get_y())
        self.set_line_width(0.2)
        self.ln(5)

    def seccion(self, titulo: str):
        self.set_fill_color(*SUAVE)
        self.set_text_color(*TINTA)
        self.set_font("Helvetica", "B", 10)
        self.cell(self.ancho, 6.5, _latin1(f"  {titulo}"), fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(1.5)

    def filas(self, pares):
        """Pares (etiqueta, valor) en dos columnas de etiqueta/valor; se omiten los vacíos."""
        pares = [(e, v) for e, v in pares if v not in (None, "")]
        mitad = self.ancho / 2
        etiqueta = 31
        ancho_valor = mitad - etiqueta - 2
        for i in range(0, len(pares), 2):
            fila = pares[i:i + 2]
            self.set_font("Helvetica", size=9.5)
            lineas = max(len(self.multi_cell(ancho_valor, 5.2, _latin1(str(v)), dry_run=True, output="LINES")) for _, v in fila)
            if self.get_y() + lineas * 5.2 > self.h - self.b_margin:
                self.add_page()
            y = self.get_y()
            for j, (e, v) in enumerate(fila):
                self.set_xy(self.l_margin + j * mitad, y)
                self.set_text_color(*GRIS)
                self.set_font("Helvetica", size=8.5)
                self.cell(etiqueta, 5.2, _latin1(e))
                self.set_text_color(25, 30, 40)
                self.set_font("Helvetica", size=9.5)
                self.multi_cell(ancho_valor, 5.2, _latin1(str(v)), align="L", new_x="RIGHT", new_y="TOP")
            self.set_xy(self.l_margin, y + lineas * 5.2 + 0.6)
        self.ln(2.5)

    def parrafo(self, texto: str, tam=9.5):
        self.set_text_color(25, 30, 40)
        self.set_font("Helvetica", size=tam)
        self.multi_cell(self.ancho, 5, _latin1(texto), align="J", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def firmas(self, izquierda: tuple[str, str, str], derecha: tuple[str, str, str]):
        """Cada lado: (rol, nombre, detalle). Deja espacio para firmar sobre la línea."""
        if self.get_y() > self.h - 62:
            self.add_page()
        self.ln(22)
        y = self.get_y()
        ancho = (self.ancho - 16) / 2
        for k, (rol, nombre, detalle) in enumerate((izquierda, derecha)):
            x = self.l_margin + k * (ancho + 16)
            self.set_draw_color(60, 60, 60)
            self.line(x, y, x + ancho, y)
            self.set_xy(x, y + 1.5)
            self.set_text_color(25, 30, 40)
            self.set_font("Helvetica", "B", 9.5)
            self.cell(ancho, 5, _latin1(nombre), align="C", new_x="LEFT", new_y="NEXT")
            self.set_font("Helvetica", size=8.5)
            self.set_text_color(*GRIS)
            self.cell(ancho, 4.5, _latin1(detalle), align="C", new_x="LEFT", new_y="NEXT")
            self.set_font("Helvetica", "B", 8.5)
            self.set_text_color(*TINTA)
            self.cell(ancho, 4.5, _latin1(rol.upper()), align="C", new_x="LEFT", new_y="NEXT")


def _nombre(empleado) -> str:
    return f"{empleado.nombres} {empleado.apellidos}"


def _identificacion(empleado) -> str:
    return f"{empleado.get_tipo_identificacion_display()} {empleado.identificacion}"


def generar_pdf_acta(acta: ActaEquipo) -> bytes:
    equipo, empleado = acta.equipo, acta.empleado
    entrega = acta.tipo == ActaEquipo.Tipo.ENTREGA
    pdf = _PDFActa(acta)
    pdf.add_page()
    pdf.encabezado("ACTA DE ENTREGA DE EQUIPO" if entrega else "ACTA DE DEVOLUCIÓN DE EQUIPO")

    pdf.seccion("Colaborador")
    pdf.filas([
        ("Nombre", _nombre(empleado)), ("Identificación", _identificacion(empleado)),
        ("Cargo", empleado.cargo.nombre), ("Ubicación", equipo.ubicacion),
    ])

    pdf.seccion("Equipo")
    ram = f"{equipo.memoria_ram_gb} GB" if equipo.memoria_ram_gb else ""
    pdf.filas([
        ("Código", equipo.codigo), ("Tipo", equipo.get_tipo_display()),
        ("Marca", equipo.marca), ("Modelo", equipo.modelo),
        ("N.º de serie", equipo.numero_serie), ("Procesador", equipo.procesador),
        ("Memoria RAM", ram), ("Almacenamiento", equipo.almacenamiento),
        ("Sistema operativo", equipo.sistema_operativo), ("Nombre de red", equipo.hostname),
        ("Dirección IP", equipo.direccion_ip), ("MAC", equipo.direccion_mac),
    ])

    pdf.seccion("Condiciones de la entrega" if entrega else "Condiciones de la devolución")
    condiciones = [("Fecha", acta.fecha.strftime("%d/%m/%Y"))]
    if entrega:
        condiciones.append(("Modalidad", "Préstamo" if acta.es_prestamo else "Asignación (uso permanente)"))
        if acta.es_prestamo:
            condiciones.append(("Devolver hasta", acta.devolucion_prevista.strftime("%d/%m/%Y")))
    elif acta.entrega_acta_id:
        condiciones.append(("Acta de entrega", f"{acta.entrega_acta.numero} del {acta.entrega_acta.fecha:%d/%m/%Y}"))
    condiciones += [("Estado físico", acta.get_condicion_display()), ("Accesorios", acta.accesorios or "Ninguno")]
    pdf.filas(condiciones)
    if acta.observacion:
        pdf.set_text_color(*GRIS)
        pdf.set_font("Helvetica", size=8.5)
        pdf.cell(pdf.ancho, 5, "Observaciones", new_x="LMARGIN", new_y="NEXT")
        pdf.parrafo(acta.observacion)

    pdf.seccion("Declaración")
    if entrega:
        plazo = PLAZO_PRESTAMO.format(fecha=fecha_texto(acta.devolucion_prevista)) if acta.es_prestamo else ""
        pdf.parrafo(CLAUSULA_ENTREGA.format(plazo=plazo))
    else:
        pdf.parrafo(CLAUSULA_DEVOLUCION)

    ti = acta.registrado_por
    lado_ti = ("Entrega · TI" if entrega else "Recibe · TI", ti.get_full_name() or ti.username, "Departamento de Tecnología")
    lado_colaborador = ("Recibe · colaborador" if entrega else "Entrega · colaborador", _nombre(empleado), _identificacion(empleado))
    pdf.firmas(lado_ti, lado_colaborador)
    return bytes(pdf.output())


def nombre_archivo(acta: ActaEquipo) -> str:
    return f"{acta.numero} {acta.get_tipo_display()} {acta.equipo.codigo} {acta.empleado.apellidos}.pdf"
