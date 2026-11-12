"""
Lectura de la hoja de vida (CV) para prellenar el registro de un empleado.

Usa reglas locales (sin servicios externos ni IA): los datos personales no salen del servidor.
Solo funciona con PDF que contengan texto; un CV escaneado o una foto no tiene texto que leer.
Lo extraído es una sugerencia: el formulario lo muestra resaltado para que el administrador lo revise.
Si el PDF es un formulario para llenar (la plantilla de la empresa), se leen los valores de sus casillas.
"""
import re
import unicodedata
from datetime import date

from pypdf import PdfReader

from apps.core import validadores

MAX_PAGINAS = 4

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dic": 12,
}

# Palabras que delatan que una línea es un título o una sección, no el nombre de la persona.
NO_ES_NOMBRE = {
    "curriculum", "vitae", "hoja", "vida", "perfil", "profesional", "datos", "personales", "informacion",
    "contacto", "experiencia", "laboral", "formacion", "academica", "educacion", "objetivo", "resumen",
    "referencias", "habilidades", "idiomas", "cursos", "certificados", "estudios", "cv",
}
PARTICULAS = {"de", "del", "la", "las", "los", "san", "santa", "y"}

RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RE_NUMERO = re.compile(r"\+?\d[\d\s\-().]{5,18}\d")
RE_FECHA_NUM = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b|\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b")
RE_FECHA_TEXTO = re.compile(r"\b(\d{1,2})\s*(?:de\s+)?([a-záéíóú]{3,10})\.?\s*(?:de\s+|del\s+)?(\d{4})\b", re.IGNORECASE)


class CVIlegible(Exception):
    """El archivo no tiene texto que se pueda leer (escaneado, imagen o PDF dañado)."""


# --------------------------------------------------------------------------- utilidades
def _sin_tildes(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def _lineas(texto: str) -> list[str]:
    """
    Una entrada por línea. Las líneas con datos separados por '|' (formato de CV de la empresa:
    'Quito, Ecuador | +593 99… | correo') se dividen para leer cada dato por separado.
    """
    lineas = []
    for linea in texto.splitlines():
        for parte in linea.split("|"):
            parte = re.sub(r"\s+", " ", parte).strip()
            if parte:
                lineas.append(parte)
    return lineas


def _valor_de_etiqueta(lineas: list[str], etiquetas: tuple[str, ...]) -> str | None:
    """Busca 'Etiqueta: valor' (o la etiqueta sola con el valor en la línea siguiente)."""
    for i, linea in enumerate(lineas):
        plano = _sin_tildes(linea)
        for etiqueta in etiquetas:
            m = re.match(rf"^\s*{etiqueta}\s*[:\-]?\s*(.*)$", plano)
            if not m:
                continue
            inicio = len(linea) - len(m.group(1))
            valor = linea[inicio:].strip(" :-*\t")
            if valor:
                return valor
            # El valor en la línea siguiente, salvo que esa línea sea otro rótulo (termina en ':'), como en un
            # formulario con la casilla vacía.
            # Tampoco una ayuda de la plantilla de la empresa (empieza con '»').
            if i + 1 < len(lineas) and not lineas[i + 1].rstrip().endswith(":") and not lineas[i + 1].startswith("»"):
                return lineas[i + 1]
            break  # el rótulo de esta línea ya coincidió (vacío): no probar uno más corto ('direccion' en 'direccion domiciliaria')
    return None


# --------------------------------------------------------------------------- lectura del PDF
def _texto_de_casillas(lector: PdfReader) -> str:
    """
    PDF para llenar (como la plantilla de hoja de vida de la empresa, apps/personal/plantilla_cv.py): lo que
    escribe el candidato está en las casillas, no en el texto de la página. Se devuelve como 'Casilla: valor',
    que es justo lo que buscan las reglas de abajo. Las referencias se ignoran: su teléfono o cédula no son
    los del candidato. La fecha de nacimiento en tres casillas (Día / Mes / Año) se une en una sola.
    """
    lineas, fecha = [], {}
    for nombre, campo in (lector.get_fields() or {}).items():
        valor = campo.get("/V")
        nombre = nombre.split(".")[-1]
        if not isinstance(valor, str) or not valor.strip() or "referencia" in _sin_tildes(nombre):
            continue
        if m := re.fullmatch(r"fecha de nacimiento\s*-\s*(dia|mes|ano)", _sin_tildes(nombre)):
            fecha[m.group(1)] = re.sub(r"\D", "", valor)
            continue
        lineas.append(f"{nombre}: {valor.strip()}")
    if all(fecha.get(p) for p in ("dia", "mes", "ano")):
        lineas.append(f"Fecha de nacimiento: {fecha['dia']}/{fecha['mes']}/{fecha['ano']}")
    return "\n".join(lineas)


def texto_de_pdf(archivo) -> str:
    try:
        archivo.seek(0)
        lector = PdfReader(archivo)
        casillas = _texto_de_casillas(lector)
        # Si el PDF trae casillas llenas, se leen solo ellas: el texto impreso del formulario son rótulos vacíos.
        texto = casillas or "\n".join((pagina.extract_text() or "") for pagina in lector.pages[:MAX_PAGINAS])
    except Exception as error:  # pypdf lanza varios tipos según el daño del archivo
        raise CVIlegible("No se pudo abrir el PDF.") from error
    finally:
        archivo.seek(0)
    if not casillas and len(re.sub(r"\s", "", texto)) < 30:
        raise CVIlegible("El PDF no tiene texto (parece escaneado).")
    return texto


# --------------------------------------------------------------------------- campos
def extraer_email(texto: str) -> str | None:
    m = RE_EMAIL.search(texto)
    return m.group(0).lower() if m else None


def _telefonos(texto: str) -> list[str]:
    encontrados = []
    for candidato in RE_NUMERO.findall(texto):
        digitos = re.sub(r"\D", "", candidato)
        if digitos.startswith("593") and len(digitos) in (11, 12):
            digitos = "0" + digitos[3:]
        if re.fullmatch(r"09\d{8}|0[2-7]\d{7}", digitos):
            encontrados.append(digitos)
    return encontrados


def extraer_telefono(texto: str) -> str | None:
    """Prefiere el celular (09…) sobre el fijo."""
    telefonos = _telefonos(texto)
    celulares = [t for t in telefonos if t.startswith("09")]
    return (celulares or telefonos or [None])[0]


def extraer_cedula(texto: str) -> str | None:
    """Primera secuencia de 10 dígitos (admite '171234567-8') con dígito verificador válido."""
    telefonos = set(_telefonos(texto))
    for candidato in re.findall(r"(?<!\d)\d{9}-?\d(?!\d)", texto):
        cedula = candidato.replace("-", "")
        if cedula not in telefonos and validadores.cedula_valida(cedula):
            return cedula
    return None


def _fecha_en(texto: str) -> date | None:
    for m in RE_FECHA_NUM.finditer(texto):
        try:
            if m.group(1):
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            continue
    for m in RE_FECHA_TEXTO.finditer(texto):
        mes = MESES.get(_sin_tildes(m.group(2)))
        if mes:
            try:
                return date(int(m.group(3)), mes, int(m.group(1)))
            except ValueError:
                continue
    return None


def extraer_fecha_nacimiento(lineas: list[str], hoy: date | None = None) -> date | None:
    hoy = hoy or date.today()
    for i, linea in enumerate(lineas):
        if not re.search(r"naci(miento|do|da)|f\.?\s*nac", _sin_tildes(linea)):
            continue
        fecha = _fecha_en(linea) or (_fecha_en(lineas[i + 1]) if i + 1 < len(lineas) else None)
        if fecha and 1940 <= fecha.year <= hoy.year - 15:
            return fecha
    return None


RE_CIUDAD_PAIS = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'\-]{2,40},\s*[A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'\-]{2,40}$")


def extraer_direccion(lineas: list[str]) -> str | None:
    """
    1.º 'Dirección: …' (o domicilio / residencia).
    2.º Sin etiqueta: la ubicación del encabezado de contacto ('Quito, Ecuador'), que al menos da la ciudad.
    """
    valor = _valor_de_etiqueta(lineas, ("direccion domiciliaria", "direccion", "domicilio", "residencia", "ubicacion"))
    if valor and not RE_EMAIL.search(valor):
        return valor[:255]
    for linea in lineas[:8]:
        if RE_CIUDAD_PAIS.match(linea) and not any(_sin_tildes(p) in NO_ES_NOMBRE for p in re.split(r"[\s,]+", linea)):
            return linea
    return None


def _es_linea_de_nombre(linea: str) -> bool:
    palabras = linea.split()
    if not 2 <= len(palabras) <= 6 or re.search(r"[\d@:/|•]", linea):
        return False
    if any(_sin_tildes(p) in NO_ES_NOMBRE for p in palabras):
        return False
    return all(re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ'\-]+", p) for p in palabras)


def _agrupar(palabras: list[str]) -> list[str]:
    """Une partículas a la palabra siguiente: 'de los Ángeles' cuenta como un solo nombre."""
    grupos, pendiente = [], []
    for palabra in palabras:
        pendiente.append(palabra)
        if _sin_tildes(palabra) not in PARTICULAS:
            grupos.append(" ".join(pendiente))
            pendiente = []
    if pendiente:
        grupos.append(" ".join(pendiente))
    return grupos


def dividir_nombre_completo(nombre: str) -> tuple[str, str]:
    """
    'Nicolás Andrés Correa Fajardo' -> ('Nicolás Andrés', 'Correa Fajardo').
    Con tres palabras se asume un nombre y dos apellidos; con dos, uno y uno.
    """
    grupos = _agrupar(nombre.split())
    if len(grupos) >= 4:
        corte = len(grupos) - 2
    elif len(grupos) == 3:
        corte = 1
    else:
        corte = 1 if len(grupos) == 2 else len(grupos)
    return " ".join(grupos[:corte]), " ".join(grupos[corte:])


def extraer_nombres(lineas: list[str]) -> tuple[str | None, str | None]:
    nombres = _valor_de_etiqueta(lineas, ("nombres",))
    apellidos = _valor_de_etiqueta(lineas, ("apellidos",))
    if nombres and apellidos and _es_linea_de_nombre(f"{nombres} {apellidos}"):
        return nombres, apellidos

    completo = _valor_de_etiqueta(lineas, ("nombres? completos?", "nombres? y apellidos", "nombre"))
    if not (completo and _es_linea_de_nombre(completo)):
        # Sin etiqueta: en casi todos los CV el nombre es una de las primeras líneas.
        completo = next((l for l in lineas[:8] if _es_linea_de_nombre(l)), None)
    if not completo:
        return None, None
    return dividir_nombre_completo(completo)


# --------------------------------------------------------------------------- punto de entrada
def extraer_datos(texto: str) -> dict:
    """Devuelve solo los campos encontrados, con las claves del formulario de empleado."""
    lineas = _lineas(texto)
    datos = {}
    nombres, apellidos = extraer_nombres(lineas)
    if nombres:
        datos["nombres"] = validadores.normalizar_nombre(nombres)
    if apellidos:
        datos["apellidos"] = validadores.normalizar_nombre(apellidos)
    if cedula := extraer_cedula(texto):
        datos["tipo_identificacion"] = "CEDULA"
        datos["identificacion"] = cedula
    if nacimiento := extraer_fecha_nacimiento(lineas):
        datos["fecha_nacimiento"] = nacimiento.isoformat()
    if telefono := extraer_telefono(texto):
        datos["telefono"] = telefono
    if email := extraer_email(texto):
        datos["email"] = email
    if direccion := extraer_direccion(lineas):
        datos["direccion"] = direccion
    return datos


def leer_cv(archivo) -> dict:
    return extraer_datos(texto_de_pdf(archivo))
