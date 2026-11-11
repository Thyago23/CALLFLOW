"""
4.1 Gestión documental › Generación de contratos a partir de plantillas.

Una plantilla es texto con variables entre llaves dobles: {{nombre_completo}}, {{identificacion}}…
Los valores salen de la ficha del empleado (que a su vez se llenó con los datos del CV al
registrarlo) y de lo que se indique al generar (fechas y salario).

Formato de la plantilla:
  - Una línea que empieza con '# ' es un título centrado.
  - **texto** se imprime en negrita.
  - Una línea en blanco separa párrafos.

Las variables se reemplazan con una expresión regular, no con el motor de plantillas de Django:
el texto lo escriben usuarios y no debe poder ejecutar nada.
"""
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.staticfiles import finders
from django.utils import timezone
from fpdf import FPDF

RE_VARIABLE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
FALTANTE = "...................."  # espacio para completar a mano si falta un dato

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


@dataclass(frozen=True)
class Variable:
    clave: str
    descripcion: str
    ejemplo: str


VARIABLES = [
    Variable("nombre_completo", "Nombres y apellidos", "María José Pérez Ñañez"),
    Variable("nombres", "Nombres", "María José"),
    Variable("apellidos", "Apellidos", "Pérez Ñañez"),
    Variable("tipo_identificacion", "Tipo de identificación", "cédula"),
    Variable("identificacion", "Número de identificación", "1710034065"),
    Variable("fecha_nacimiento", "Fecha de nacimiento", "20/04/1995"),
    Variable("direccion", "Dirección", "Av. Amazonas y Colón, Quito"),
    Variable("telefono", "Teléfono", "0991234567"),
    Variable("email", "Correo electrónico", "maria.perez@correo.com"),
    Variable("cargo", "Cargo", "Asesor de ventas"),
    Variable("tipo_contrato", "Tipo de contrato", "indefinido"),
    Variable("fecha_ingreso", "Fecha de ingreso", "05/11/2026"),
    Variable("fecha_inicio", "Inicio del contrato", "05/11/2026"),
    Variable("fecha_inicio_texto", "Inicio del contrato, en letras", "5 de noviembre de 2026"),
    Variable("fecha_fin", "Fin del contrato (si tiene plazo)", "04/11/2027"),
    Variable("salario", "Salario mensual", "$ 482,00"),
    Variable("salario_en_letras", "Salario en letras", "CUATROCIENTOS OCHENTA Y DOS DÓLARES CON 00/100"),
    Variable("fecha_actual", "Fecha de hoy", "05/11/2026"),
    Variable("fecha_actual_texto", "Fecha de hoy, en letras", "5 de noviembre de 2026"),
]
CLAVES = {v.clave for v in VARIABLES}


# --------------------------------------------------------------------------- formatos
def fecha_corta(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else ""


def fecha_texto(valor: date | None) -> str:
    return f"{valor.day} de {MESES[valor.month - 1]} de {valor.year}" if valor else ""


def dinero(valor: Decimal | None) -> str:
    if valor is None:
        return ""
    entero, decimales = f"{valor.quantize(Decimal('0.01'), ROUND_HALF_UP):.2f}".split(".")
    return f"$ {int(entero):,}".replace(",", ".") + f",{decimales}"


_UNIDADES = ["", "UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE", "DIEZ",
             "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISÉIS", "DIECISIETE", "DIECIOCHO",
             "DIECINUEVE", "VEINTE", "VEINTIUNO", "VEINTIDÓS", "VEINTITRÉS", "VEINTICUATRO",
             "VEINTICINCO", "VEINTISÉIS", "VEINTISIETE", "VEINTIOCHO", "VEINTINUEVE"]
_DECENAS = ["", "", "", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
_CENTENAS = ["", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS",
             "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]


def _hasta_999(n: int) -> str:
    if n == 100:
        return "CIEN"
    centena, resto = divmod(n, 100)
    partes = [_CENTENAS[centena]] if centena else []
    if resto < 30:
        partes.append(_UNIDADES[resto])
    else:
        decena, unidad = divmod(resto, 10)
        partes.append(_DECENAS[decena] + (f" Y {_UNIDADES[unidad]}" if unidad else ""))
    return " ".join(p for p in partes if p)


def numero_en_letras(n: int) -> str:
    """0 … 999.999.999 en letras (español)."""
    if n == 0:
        return "CERO"
    millones, resto = divmod(n, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    partes = []
    if millones:
        partes.append("UN MILLÓN" if millones == 1 else f"{_hasta_999(millones)} MILLONES")
    if miles:
        partes.append("MIL" if miles == 1 else f"{_hasta_999(miles)} MIL")
    if unidades:
        partes.append(_hasta_999(unidades))
    texto = " ".join(partes)
    return re.sub(r"\bUNO (MIL|MILLONES)\b", r"UN \1", texto).replace("VEINTIUNO MIL", "VEINTIÚN MIL")


def dinero_en_letras(valor: Decimal | None) -> str:
    if valor is None:
        return ""
    valor = valor.quantize(Decimal("0.01"), ROUND_HALF_UP)
    entero = int(valor)
    centavos = int((valor - entero) * 100)
    moneda = "DÓLAR" if entero == 1 else "DÓLARES"
    # Delante de un sustantivo se apocopa: "un dólar", "veintiún dólares", "treinta y un dólares".
    letras = re.sub(r"VEINTIUNO$", "VEINTIÚN", numero_en_letras(entero))
    letras = re.sub(r"(?<![A-Z])UNO$", "UN", letras)
    return f"{letras} {moneda} CON {centavos:02d}/100"


# --------------------------------------------------------------------------- datos y reemplazo
def variables_en(contenido: str) -> set[str]:
    return set(RE_VARIABLE.findall(contenido or ""))


def variables_desconocidas(contenido: str) -> set[str]:
    return variables_en(contenido) - CLAVES


def datos_del_empleado(empleado, *, fecha_inicio: date | None, fecha_fin: date | None, salario: Decimal | None) -> dict:
    """Todos los valores disponibles, ya con formato de texto. Vacío = dato que falta."""
    hoy = timezone.localdate()
    return {
        "nombre_completo": f"{empleado.nombres} {empleado.apellidos}".strip(),
        "nombres": empleado.nombres,
        "apellidos": empleado.apellidos,
        "tipo_identificacion": empleado.get_tipo_identificacion_display().lower(),
        "identificacion": empleado.identificacion,
        "fecha_nacimiento": fecha_corta(empleado.fecha_nacimiento),
        "direccion": empleado.direccion,
        "telefono": empleado.telefono,
        "email": empleado.email,
        "cargo": str(empleado.cargo),
        "tipo_contrato": empleado.get_tipo_contrato_display().lower(),
        "fecha_ingreso": fecha_corta(empleado.fecha_ingreso),
        "fecha_inicio": fecha_corta(fecha_inicio),
        "fecha_inicio_texto": fecha_texto(fecha_inicio),
        "fecha_fin": fecha_corta(fecha_fin),
        "salario": dinero(salario),
        "salario_en_letras": dinero_en_letras(salario),
        "fecha_actual": fecha_corta(hoy),
        "fecha_actual_texto": fecha_texto(hoy),
    }


def datos_de_ejemplo() -> dict:
    return {v.clave: v.ejemplo for v in VARIABLES}


def faltantes(contenido: str, datos: dict) -> list[Variable]:
    usadas = variables_en(contenido)
    return [v for v in VARIABLES if v.clave in usadas and not datos.get(v.clave)]


def _limpiar_valor(valor: str) -> str:
    # Un valor nunca debe activar el formato de la plantilla (negrita, títulos).
    return re.sub(r"[*#_\-]{2,}", "", str(valor)).replace("\n", " ").strip()


def llenar(contenido: str, datos: dict) -> str:
    def reemplazo(m):
        valor = datos.get(m.group(1))
        return _limpiar_valor(valor) if valor else FALTANTE
    return RE_VARIABLE.sub(reemplazo, contenido or "")


# --------------------------------------------------------------------------- PDF
_EQUIVALENCIAS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "...", "•": "-", " ": " "})


def _latin1(texto: str) -> str:
    """La fuente estándar del PDF cubre español (tildes, ñ, ¿, ¡) pero no comillas tipográficas."""
    return texto.translate(_EQUIVALENCIAS).encode("latin-1", "replace").decode("latin-1")


class _PDFContrato(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", size=8)
        self.set_text_color(120)
        self.cell(0, 10, f"Página {self.page_no()} de {{nb}}", align="C")


def generar_pdf(texto: str, *, titulo: str = "Contrato") -> bytes:
    pdf = _PDFContrato(format="A4")
    pdf.set_title(_latin1(titulo))
    pdf.set_creator("CALLFLOW")
    pdf.set_margins(22, 20, 22)
    pdf.set_auto_page_break(True, margin=22)
    pdf.add_page()

    logo = finders.find("img/logo-multivisa.png")
    if logo:
        pdf.image(logo, x=pdf.w - 22 - 26, y=12, w=26)
        pdf.set_y(40)

    for bloque in re.split(r"\n\s*\n", texto.strip()):
        # '--' es subrayado para fpdf2: se deja como guion simple. Las rayas de firma (____) se conservan.
        bloque = re.sub(r"-{2,}", "-", _latin1(bloque.strip()))
        if not bloque:
            continue
        if bloque.startswith("# "):
            pdf.set_font("Helvetica", "B", 13)
            pdf.multi_cell(0, 7, bloque[2:].strip(), align="C", markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
        else:
            # Los saltos de línea simples se respetan (bloques de firma, listas).
            if bloque.startswith("___"):
                pdf.ln(14)  # espacio para firmar sobre la línea
            pdf.set_font("Helvetica", size=10.5)
            pdf.multi_cell(0, 5.6, bloque, align="J", markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3.5)
    return bytes(pdf.output())


# --------------------------------------------------------------------------- generación y guardado
def salario_vigente(empleado, fecha: date | None = None) -> Decimal | None:
    from django.db.models import Q

    fecha = fecha or timezone.localdate()
    registro = (
        empleado.salarios.filter(vigente_desde__lte=fecha)
        .filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=fecha))
        .order_by("-vigente_desde").first()
    )
    return registro.salario_base if registro else None


def generar_contrato(empleado, plantilla, *, fecha_inicio: date, fecha_fin: date | None, salario: Decimal | None, usuario):
    """
    Llena la plantilla, crea el PDF y lo guarda en el expediente del empleado (tipo CONTRATO),
    con el registro de qué plantilla y qué datos se usaron. Debe llamarse dentro de una transacción.
    """
    from django.core.files.base import ContentFile

    from .models import ContratoGenerado
    from .servicios import guardar_documento

    datos = datos_del_empleado(empleado, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, salario=salario)
    texto = llenar(plantilla.contenido, datos)
    titulo = f"{plantilla.nombre} - {datos['nombre_completo']}"
    pdf = generar_pdf(texto, titulo=titulo)

    nombre = f"Contrato {empleado.apellidos} {empleado.nombres} {fecha_inicio:%Y-%m-%d}.pdf"
    documento = guardar_documento(
        ContentFile(pdf, name=nombre), tipo_codigo="CONTRATO", empleado=empleado, usuario=usuario,
        descripcion=f"{plantilla.nombre} v{plantilla.version}",
    )
    usadas = variables_en(plantilla.contenido)
    return ContratoGenerado.objects.create(
        empleado=empleado, plantilla=plantilla, documento=documento,
        datos_utilizados={clave: valor for clave, valor in datos.items() if clave in usadas},
        fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, generado_por=usuario,
    )
