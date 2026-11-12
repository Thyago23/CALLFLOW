"""
4.1 Gestión documental › Generación de contratos a partir de plantillas.

Una plantilla es texto con variables entre llaves dobles: {{nombre_completo}}, {{identificacion}}…
Los valores salen de la ficha del empleado (que a su vez se llenó con los datos del CV al
registrarlo) y de lo que se indique al generar (fechas y salario).

Formato de la plantilla (una línea en blanco separa bloques):
  - '# Título' es un título centrado (varias líneas seguidas = título de varias líneas).
  - '## Cláusula primera. - Antecedentes' es el encabezado de una cláusula.
  - Un bloque que empieza con 'A. ', 'B. ' o '1.1 ' es un numeral con sangría (el número va en negrita).
  - Un bloque de líneas '| celda | celda |' es una tabla con bordes (la primera fila es el encabezado).
  - '[[espacio]]' deja espacio en blanco (por ejemplo, para firmar).
  - **texto** se imprime en negrita.
Estilo «formal» (el del Word de la empresa): Times New Roman, títulos en azul y márgenes del Word, sin logo.

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
    # Las que usa el contrato de servicios profesionales (Word de la empresa).
    Variable("nombre_completo_mayusculas", "Apellidos y nombres, en mayúsculas", "PÉREZ ÑAÑEZ MARÍA JOSÉ"),
    Variable("tipo_identificacion_mayusculas", "Tipo de identificación, en mayúsculas", "CÉDULA"),
    Variable("fecha_inicio_legal", "Inicio del contrato, como en los contratos", "05 de noviembre del 2026"),
    Variable("salario_corto", "Salario mensual, sin centavos si son cero", "$400"),
    # Las que usa el contrato de imagen corporativa (Word de la empresa).
    Variable("identificacion_con_guion", "Cédula con guion antes del último dígito", "171003406-5"),
    Variable("fecha_inicio_dia", "Día del inicio del contrato (dos cifras)", "09"),
    Variable("fecha_inicio_mes", "Mes del inicio del contrato", "noviembre"),
    Variable("fecha_inicio_anio", "Año del inicio del contrato", "2026"),
    Variable("fecha_inicio_anio_letras", "Año del inicio del contrato, en letras", "dos mil veintiséis"),
    Variable("plazo_meses", "Duración del contrato en meses (de la fecha de inicio a la de fin)", "3"),
    Variable("salario_letras_texto", "Salario en letras, como en los contratos", "trescientos dólares de los Estados Unidos de América"),
]
CLAVES = {v.clave for v in VARIABLES}


# --------------------------------------------------------------------------- formatos
def fecha_corta(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else ""


def fecha_texto(valor: date | None) -> str:
    return f"{valor.day} de {MESES[valor.month - 1]} de {valor.year}" if valor else ""


def fecha_legal(valor: date | None) -> str:
    """05/11/2026 -> '05 de noviembre del 2026' (como se escribe en los contratos de la empresa)."""
    return f"{valor.day:02d} de {MESES[valor.month - 1]} del {valor.year}" if valor else ""


def con_guion(cedula: str) -> str:
    """1721129789 -> '172112978-9' (así se escribe la cédula en los contratos)."""
    return f"{cedula[:-1]}-{cedula[-1]}" if len(cedula) == 10 and cedula.isdigit() else cedula


def plazo_en_meses(inicio: date | None, fin: date | None) -> str:
    """Meses completos de duración: del 09/11/2026 al 08/02/2027 son 3."""
    if not inicio or not fin or fin < inicio:
        return ""
    return str(max(round((fin - inicio).days / 30.44), 1))


def dinero_corto(valor: Decimal | None) -> str:
    """400 -> '$400'; 400.5 -> '$400,50'; 1300 -> '$1.300'."""
    if valor is None:
        return ""
    valor = valor.quantize(Decimal("0.01"), ROUND_HALF_UP)
    entero, decimales = f"{valor:.2f}".split(".")
    texto = f"${int(entero):,}".replace(",", ".")
    return texto if decimales == "00" else f"{texto},{decimales}"


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


def dinero_en_letras_texto(valor: Decimal | None) -> str:
    """300 -> 'trescientos dólares de los Estados Unidos de América' (con '50/100' si hay centavos)."""
    if valor is None:
        return ""
    valor = valor.quantize(Decimal("0.01"), ROUND_HALF_UP)
    entero, centavos = int(valor), int((valor - int(valor)) * 100)
    letras = re.sub(r"VEINTIUNO$", "VEINTIÚN", numero_en_letras(entero))
    letras = re.sub(r"(?<![A-Z])UNO$", "UN", letras).lower()
    moneda = "dólar" if entero == 1 else "dólares"
    return f"{letras} {moneda}{f' con {centavos:02d}/100' if centavos else ''} de los Estados Unidos de América"


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
        "nombre_completo_mayusculas": f"{empleado.apellidos} {empleado.nombres}".strip().upper(),
        "tipo_identificacion_mayusculas": empleado.get_tipo_identificacion_display().upper(),
        "fecha_inicio_legal": fecha_legal(fecha_inicio),
        "salario_corto": dinero_corto(salario),
        "identificacion_con_guion": con_guion(empleado.identificacion) if empleado.tipo_identificacion == "CEDULA" else empleado.identificacion,
        "fecha_inicio_dia": f"{fecha_inicio.day:02d}" if fecha_inicio else "",
        "fecha_inicio_mes": MESES[fecha_inicio.month - 1] if fecha_inicio else "",
        "fecha_inicio_anio": str(fecha_inicio.year) if fecha_inicio else "",
        "fecha_inicio_anio_letras": numero_en_letras(fecha_inicio.year).lower() if fecha_inicio else "",
        "plazo_meses": plazo_en_meses(fecha_inicio, fecha_fin),
        "salario_letras_texto": dinero_en_letras_texto(salario),
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


RE_NUMERAL = re.compile(r"^(?:\*\*)?([A-Z]\.|\d+\.\d+)(?:\*\*)?\s+(.*)$", re.S)


def bloques(texto: str) -> list[tuple[str, object]]:
    """
    Divide el texto ya llenado en bloques con su tipo. Lo usan el PDF y la vista previa, así se ven igual:
    ('titulo', [líneas]) · ('clausula', texto) · ('numeral', (número, texto)) · ('tabla', [[celdas]])
    · ('espacio', None) · ('firma', texto) · ('parrafo', texto).
    """
    resultado = []
    for bloque in re.split(r"\n\s*\n", (texto or "").strip()):
        bloque = bloque.strip()
        if not bloque:
            continue
        lineas = [l.strip() for l in bloque.split("\n")]
        if all(l.startswith("# ") for l in lineas):
            resultado.append(("titulo", [l[2:].strip() for l in lineas]))
        elif bloque.startswith("## "):
            resultado.append(("clausula", bloque[3:].strip()))
        elif all(l.startswith("|") for l in lineas):
            resultado.append(("tabla", [[c.strip() for c in l.strip("|").split("|")] for l in lineas]))
        elif bloque == "[[espacio]]":
            resultado.append(("espacio", None))
        elif bloque.startswith("___"):
            resultado.append(("firma", bloque))
        elif m := RE_NUMERAL.match(bloque):
            resultado.append(("numeral", (m.group(1), m.group(2).strip())))
        else:
            resultado.append(("parrafo", bloque))
    return resultado


# Estilo «formal»: copia del Word «Contrato de servicios profesionales» (Times New Roman, títulos en azul).
AZUL_WORD = (47, 84, 150)
ESTILOS = {
    "SIMPLE": {"fuente": "Helvetica", "cuerpo": 10.5, "linea": 5.6, "margenes": (22, 20, 22), "logo": True,
               "titulo": ("Helvetica", "B", 13, (0, 0, 0)), "clausula": ("Helvetica", "B", 10.5, (0, 0, 0)), "sangria": 0},
    "FORMAL": {"fuente": "Times", "cuerpo": 12, "linea": 5.0, "margenes": (25, 20, 25), "logo": False,
               "titulo": ("Times", "", 16, AZUL_WORD), "clausula": ("Times", "B", 13, AZUL_WORD), "sangria": 9},
    # Como el Word «Contrato de servicios profesionales de imagen corporativa y visual»: Calibri 11 en negro,
    # numerales en línea («1.1 texto») y los comparecientes (A., B.) con sangría.
    "OFICIO": {"fuente": "Calibri", "cuerpo": 11, "linea": 5.2, "margenes": (25, 25, 25), "logo": False,
               "titulo": ("Calibri", "B", 11, (0, 0, 0)), "clausula": ("Calibri", "B", 11, (0, 0, 0)), "sangria": 10,
               "numeral_en_linea": True, "espacio": 28},
}

# Calibri no se puede incluir en el proyecto (licencia de Microsoft): se usa la del equipo si está instalada, o
# Carlito, su equivalente libre con las mismas medidas. Si no hay ninguna, Helvetica.
_CALIBRI = [
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ("/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf", "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf"),
    ("/usr/share/fonts/crosextra/Carlito-Regular.ttf", "/usr/share/fonts/crosextra/Carlito-Bold.ttf"),
    ("/usr/share/fonts/google-carlito-fonts/Carlito-Regular.ttf", "/usr/share/fonts/google-carlito-fonts/Carlito-Bold.ttf"),
]


def _cargar_fuentes(pdf, e: dict) -> dict:
    """Registra Calibri/Carlito si el estilo la pide; si no la encuentra, cambia el estilo a Helvetica."""
    if "Calibri" not in (e["fuente"], e["titulo"][0], e["clausula"][0]):
        return e
    import os

    for normal, negrita in _CALIBRI:
        if os.path.exists(normal) and os.path.exists(negrita):
            pdf.add_font("Calibri", "", normal)
            pdf.add_font("Calibri", "B", negrita)
            return e
    cambiar = lambda t: ("Helvetica", *t[1:]) if t[0] == "Calibri" else t  # noqa: E731
    return {**e, "fuente": "Helvetica", "cuerpo": e["cuerpo"] - 0.5, "titulo": cambiar(e["titulo"]), "clausula": cambiar(e["clausula"])}


def _mantener_junto(pdf, alto: float) -> None:
    """Si el bloque no cabe en lo que queda de la página, empieza en la siguiente (tablas y firmas no se parten)."""
    if pdf.get_y() + alto > pdf.page_break_trigger:
        pdf.add_page()


def _sin_subrayado(texto: str) -> str:
    # '--' es subrayado para el markdown de fpdf2: se deja como guion simple.
    return re.sub(r"-{2,}", "-", _latin1(texto))


def generar_pdf(texto: str, *, titulo: str = "Contrato", estilo: str = "SIMPLE") -> bytes:
    e = ESTILOS.get(estilo, ESTILOS["SIMPLE"])
    pdf = _PDFContrato(format="A4")
    e = _cargar_fuentes(pdf, e)
    pdf.set_title(_latin1(titulo))
    pdf.set_creator("CALLFLOW")
    izquierda, arriba, derecha = e["margenes"]
    pdf.set_margins(izquierda, arriba, derecha)
    pdf.set_auto_page_break(True, margin=22)
    pdf.add_page()

    if e["logo"]:
        logo = finders.find("img/logo-multivisa.png")
        if logo:
            pdf.image(logo, x=pdf.w - derecha - 26, y=12, w=26)
            pdf.set_y(40)

    ancho = pdf.w - izquierda - derecha
    for tipo, valor in bloques(texto):
        pdf.set_text_color(0)
        if tipo == "titulo":
            fuente, negrita, tamano, color = e["titulo"]
            pdf.set_font(fuente, negrita, tamano)
            pdf.set_text_color(*color)
            for linea in valor:
                pdf.multi_cell(0, tamano * 0.45, _sin_subrayado(linea), align="C", markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
        elif tipo == "clausula":
            fuente, negrita, tamano, color = e["clausula"]
            _mantener_junto(pdf, 6.5 + 4 * e["linea"])  # el encabezado no queda solo al pie de la página
            pdf.ln(2)
            pdf.set_font(fuente, negrita, tamano)
            pdf.set_text_color(*color)
            pdf.multi_cell(0, 6.5, _sin_subrayado(valor).replace("**", ""), align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.5)
        elif tipo == "numeral" and e.get("numeral_en_linea"):
            numero, cuerpo = valor
            sangria = e["sangria"] if numero[0].isalpha() else 0  # A., B. con sangría; 1.1 al margen
            pdf.set_x(izquierda + sangria)
            pdf.set_font(e["fuente"], "", e["cuerpo"])
            pdf.multi_cell(ancho - sangria, e["linea"], _sin_subrayado(f"{numero} {cuerpo}"), align="J", markdown=True,
                           new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2.5)
        elif tipo == "numeral":
            numero, cuerpo = valor
            pdf.set_x(izquierda + e["sangria"])
            pdf.set_font("Helvetica", "B", e["cuerpo"] - 1)
            pdf.cell(9 if len(numero) <= 3 else 11, e["linea"], _latin1(numero))
            pdf.set_font(e["fuente"], "", e["cuerpo"])
            pdf.multi_cell(pdf.w - derecha - pdf.get_x(), e["linea"], _sin_subrayado(cuerpo), align="J", markdown=True,
                           new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif tipo == "tabla":
            pdf.ln(1)
            columnas = max(len(f) for f in valor)
            ancho_tabla = min(150, ancho - 2 * e["sangria"])
            _mantener_junto(pdf, len(valor) * (e["linea"] + 0.6))
            pdf.set_draw_color(0)
            for i, fila in enumerate(valor):
                pdf.set_x(izquierda + (ancho - ancho_tabla) / 2)
                pdf.set_font(e["fuente"], "B" if i == 0 else "", e["cuerpo"])
                for celda in fila + [""] * (columnas - len(fila)):
                    pdf.cell(ancho_tabla / columnas, e["linea"] + 0.6, _latin1(celda.replace("**", "")), border=1, align="C")
                pdf.ln()
            pdf.ln(3)
        elif tipo == "espacio":
            pdf.ln(e.get("espacio", 13))
        else:
            if tipo == "firma":
                pdf.ln(14)  # espacio para firmar sobre la línea
            sangria = e["sangria"] if tipo == "parrafo" and estilo == "FORMAL" and "\n" in valor else 0
            if "\n" in valor and len(valor) < 400:  # bloques cortos de varias líneas (firmas): no se parten
                _mantener_junto(pdf, (valor.count("\n") + 1) * e["linea"])
            pdf.set_x(izquierda + sangria)
            pdf.set_font(e["fuente"], size=e["cuerpo"])
            # Los saltos de línea simples se respetan (bloques de firma, listas).
            pdf.multi_cell(ancho - sangria, e["linea"], _sin_subrayado(valor), align="J", markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
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
    pdf = generar_pdf(texto, titulo=titulo, estilo=plantilla.estilo)

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
