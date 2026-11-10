"""
Carga de bases comerciales desde Excel (SCRUM-93): las deja en el formato de la empresa.

    CIUDAD · CATEGORIA TARJETA · DIRECCION · NOMBRE Y APELLIDO · CEDULA · NUMERO

Las bases llegan de distintos proveedores, normalmente SIN encabezados ni títulos. Aquí:
- si el archivo trae encabezados, cada columna se reconoce por sinónimos («Teléfono», «Celular»,
  «CI», «Cliente»…), sin importar mayúsculas, tildes ni orden;
- si no los trae, cada columna se reconoce por su contenido: cédulas válidas, teléfonos,
  marcas y tipos de tarjeta, ciudades del Ecuador, direcciones y nombres; lo que no se pueda
  deducir en un archivo de 6 columnas se toma del orden del formato de la empresa;
- «Nombres» y «Apellidos» separados se unen y si hay dos columnas de categoría se juntan;
- se limpian los valores: espacios, Mayúsculas Iniciales, cédulas a las que Excel les quitó
  el 0 inicial, teléfonos con 593 o sin el 0;
- se omiten filas vacías y contactos sin un teléfono válido;
- los repetidos dentro del archivo (misma cédula válida o algún teléfono igual) se separan en
  `Resultado.repetidas` para enviarlos a la base de repetidos (servicios.importar_base).
"""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from django.core.exceptions import ValidationError

from apps.core import validadores

ENCABEZADOS = ["CIUDAD", "CATEGORIA TARJETA", "DIRECCION", "NOMBRE Y APELLIDO", "CEDULA", "NUMERO"]
CAMPOS = ["ciudad", "categoria_tarjeta", "direccion", "nombre", "cedula", "numero"]
MAX_FILAS = 50_000
FILAS_PARA_ENCABEZADO = 25

# Sinónimos de cada columna (ya normalizados: minúsculas, sin tildes ni signos).
SINONIMOS = {
    "cedula": ["cedula", "ci", "c i", "cedula de identidad", "cedula identidad", "identificacion", "documento", "nro cedula",
               "numero cedula", "numero de cedula", "num cedula", "ruc", "dni", "id cliente", "cedula ruc"],
    "categoria_tarjeta": ["categoria tarjeta", "categoria de tarjeta", "categoria", "tipo tarjeta", "tipo de tarjeta", "tarjeta",
                          "cat tarjeta", "marca tarjeta", "producto", "segmento"],
    "ciudad": ["ciudad", "city", "canton", "localidad", "ciudad residencia", "ciudad domicilio"],
    "direccion": ["direccion", "dir", "domicilio", "direccion domicilio", "direccion de domicilio", "address", "calle",
                  "direccion residencia"],
    "apellido": ["apellido", "apellidos", "primer apellido"],
    "nombre": ["nombre y apellido", "nombres y apellidos", "apellidos y nombres", "nombre completo", "nombre", "nombres",
               "cliente", "titular", "razon social", "nombre cliente", "nombre del cliente"],
    "numero": ["numero", "telefono", "celular", "movil", "telf", "tel", "fono", "contacto", "numero telefono",
               "numero de telefono", "numero celular", "numero de celular", "whatsapp", "telefono celular", "cel"],
}
_PARTICULAS = {"de", "del", "la", "las", "los", "y", "e"}


def normalizar(texto) -> str:
    """'Teléfono N°1' -> 'telefono n 1'."""
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texto).split())


def campo_de(encabezado: str) -> str | None:
    """Columna del formato a la que corresponde un encabezado (None si no se reconoce)."""
    texto = normalizar(encabezado)
    if not texto:
        return None
    palabras = set(texto.split())
    mejor, puntaje = None, 0
    for campo, sinonimos in SINONIMOS.items():
        for s in sinonimos:
            if texto == s:
                valor = 100 + len(s)
            elif set(s.split()) <= palabras:
                valor = len(s.split())
            else:
                continue
            if valor > puntaje:
                mejor, puntaje = campo, valor
    return mejor


# --------------------------------------------------------------------------- limpieza de valores
def texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    if isinstance(valor, datetime | date):
        return valor.strftime("%d/%m/%Y")
    return " ".join(str(valor).replace("\xa0", " ").split())


def titulo(valor: str) -> str:
    """'JUAN DE LA TORRE' -> 'Juan de la Torre'; 'av. amazonas n34-12' -> 'Av. Amazonas N34-12'."""
    palabras = texto(valor).lower().split()
    return " ".join(p if i and p in _PARTICULAS else p[:1].upper() + p[1:] for i, p in enumerate(palabras))


def cedula(valor) -> tuple[str, bool]:
    """(cédula limpia, es válida). Recupera el 0 inicial que Excel quita a las de Azuay, Bolívar…"""
    digitos = re.sub(r"\D", "", texto(valor))
    if len(digitos) in (9, 12):
        digitos = "0" + digitos
    if len(digitos) == 10:
        return digitos, validadores.cedula_valida(digitos)
    if len(digitos) == 13:
        return digitos, digitos.endswith("001")
    return digitos[:13], False


def telefono(valor) -> str:
    """Un teléfono ecuatoriano con su 0 inicial, o '' si no lo es: 593991234567 / 991234567 -> 0991234567."""
    digitos = re.sub(r"\D", "", str(valor))
    if digitos.startswith("593") and len(digitos) in (11, 12):
        digitos = "0" + digitos[3:]
    elif len(digitos) == 9 and digitos[0] == "9":
        digitos = "0" + digitos
    elif len(digitos) == 8 and digitos[0] in "234567":
        digitos = "0" + digitos
    return digitos if len(digitos) in (9, 10) and digitos[0] == "0" else ""


def telefonos(*valores) -> list[str]:
    """Todos los teléfonos válidos de una o varias celdas ('0991234567 / 022345678'), sin repetir."""
    vistos = []
    for valor in valores:
        crudo = texto(valor)
        partes = re.split(r"[/,;|]|\s{2,}|\sy\s|\s-\s", crudo) if crudo else []
        if len(partes) == 1 and len(re.sub(r"\D", "", crudo)) > 12:  # dos números separados por un espacio
            partes = crudo.split()
        for parte in partes:
            numero = telefono(parte)
            if numero and numero not in vistos:
                vistos.append(numero)
    return vistos


# --------------------------------------------------------------------------- lectura del archivo
def leer_filas(archivo) -> list[list]:
    """Filas de la primera hoja con datos (.xlsx, .xls o .csv)."""
    nombre = (getattr(archivo, "name", "") or "").lower()
    contenido = archivo.read()
    try:
        if nombre.endswith((".xlsx", ".xlsm")):
            from openpyxl import load_workbook

            libro = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
            for hoja in libro.worksheets:
                filas = [list(f) for f in hoja.iter_rows(values_only=True)]
                if any(any(c not in (None, "") for c in f) for f in filas):
                    return filas
            return []
        if nombre.endswith(".xls"):
            import xlrd

            libro = xlrd.open_workbook(file_contents=contenido)
            for hoja in libro.sheets():
                if hoja.nrows:
                    return [hoja.row_values(i) for i in range(hoja.nrows)]
            return []
        if nombre.endswith((".csv", ".txt")):
            for codificacion in ("utf-8-sig", "latin-1"):
                try:
                    decodificado = contenido.decode(codificacion)
                    break
                except UnicodeDecodeError:
                    continue
            try:
                dialecto = csv.Sniffer().sniff(decodificado[:4096], delimiters=",;\t|")
            except csv.Error:
                dialecto = csv.excel
            return [fila for fila in csv.reader(io.StringIO(decodificado), dialecto)]
    except Exception as error:  # noqa: BLE001 - un archivo dañado no debe dar error 500
        raise ValidationError("No se pudo leer el archivo. Verifique que no esté dañado ni protegido con contraseña.") from error
    raise ValidationError("Formato no admitido. Suba un Excel (.xlsx o .xls) o un .csv.")


@dataclass
class Resultado:
    filas: list = field(default_factory=list)  # dicts con CAMPOS + cedula_valida, numero_alterno, fila
    columnas: dict = field(default_factory=dict)  # campo -> encabezado original
    leidas: int = 0
    vacias: int = 0
    sin_telefono: int = 0
    repetidas: list = field(default_factory=list)  # como `filas` + repite_fila y criterio (CEDULA / NUMERO)
    cedulas_invalidas: int = 0
    fila_encabezado: int = 0

    @property
    def importadas(self) -> int:
        return len(self.filas)

    @property
    def duplicadas(self) -> int:
        return len(self.repetidas)

    def resumen(self) -> dict:
        return {"leidas": self.leidas, "importadas": self.importadas, "vacias": self.vacias,
                "sin_telefono": self.sin_telefono, "duplicadas": self.duplicadas,
                "cedulas_invalidas": self.cedulas_invalidas, "columnas": self.columnas, "fila_encabezado": self.fila_encabezado}


def _encabezado(filas) -> tuple[int, dict]:
    """(índice de la fila de encabezados, {índice de columna: campo}); (-1, {}) si el archivo no trae encabezados."""
    mejor, columnas_mejor = -1, {}
    for i, fila in enumerate(filas[:FILAS_PARA_ENCABEZADO]):
        if any(telefonos(celda) for celda in fila):
            break  # empezaron los datos: los encabezados siempre van antes (una celda «sin número» no es encabezado)
        llenas = [celda for celda in fila if texto(celda)]
        columnas = {j: c for j, celda in enumerate(fila) if (c := campo_de(celda))}
        reconocidos = set(columnas.values())
        if ("numero" in reconocidos and len(reconocidos) >= 2 and len(columnas) * 2 >= len(llenas)
                and len(reconocidos) > len(set(columnas_mejor.values()))):
            mejor, columnas_mejor = i, columnas
    return mejor, columnas_mejor


# --------------------------------------------------------------------------- columnas sin encabezado: por su contenido
MUESTRA = 300
UMBRAL = 0.5
PALABRAS_TARJETA = {
    "visa", "mastercard", "master", "diners", "amex", "american", "express", "discover", "oro", "gold", "platinum", "platino",
    "black", "clasica", "classic", "signature", "infinite", "titanium", "credito", "debito", "pacificard", "union", "cuotafacil",
    "electron", "maestro", "premium", "world", "elite", "internacional", "nacional", "club",
}
PALABRAS_DIRECCION = {
    "av", "avenida", "calle", "cdla", "ciudadela", "mz", "manzana", "villa", "sector", "barrio", "km", "urb", "urbanizacion",
    "lote", "conjunto", "edificio", "edif", "piso", "dpto", "departamento", "casa", "pasaje", "psje", "coop", "cooperativa",
    "via", "entre", "diagonal", "transversal", "junto", "frente", "esquina", "parroquia", "recinto", "solar", "bloque",
}
CIUDADES = {normalizar(c) for c in [
    "Quito", "Guayaquil", "Cuenca", "Santo Domingo", "Machala", "Durán", "Manta", "Portoviejo", "Loja", "Ambato", "Esmeraldas",
    "Quevedo", "Riobamba", "Milagro", "Ibarra", "La Libertad", "Babahoyo", "Sangolquí", "Daule", "Latacunga", "Tulcán", "Chone",
    "Pasaje", "Santa Elena", "El Carmen", "Huaquillas", "Nueva Loja", "Lago Agrio", "Santa Rosa", "Cayambe", "Otavalo", "Azogues",
    "Puyo", "Tena", "Macas", "Zamora", "Guaranda", "Salinas", "Playas", "Samborondón", "La Troncal", "Rumiñahui", "Balzar",
    "Jipijapa", "Montecristi", "El Empalme", "Vinces", "Ventanas", "Naranjal", "El Triunfo", "Yaguachi", "Shushufindi", "Coca",
    "Francisco de Orellana", "Puerto Ayora", "Atuntaqui", "Cotacachi", "Pedro Carbo", "Valencia", "La Maná", "Salcedo", "Pujilí",
    "Gualaceo", "Paute", "Catamayo", "Macará", "Sucúa", "Pelileo", "Baños", "Píllaro", "Alausí", "Guano", "Bahía de Caráquez",
    "Sucre", "Rocafuerte", "Pedernales", "Atacames", "Quinindé", "Machachi", "Mejía", "Tabacundo", "Pedro Moncayo", "Conocoto",
    "Cumbayá", "Tumbaco", "Calderón", "Pomasqui", "Nobol", "Salitre", "Buena Fe", "Montalvo", "Arenillas", "Piñas", "Zaruma",
    "Cariamanga", "Saraguro", "Gualaquiza", "Yantzaza", "Biblián", "Cañar", "San Miguel", "Caluma", "Echeandía", "San Gabriel",
    "El Ángel", "Puerto Baquerizo Moreno",
    # provincias (algunas bases traen la provincia en vez de la ciudad)
    "Pichincha", "Guayas", "Azuay", "Manabí", "El Oro", "Los Ríos", "Tungurahua", "Chimborazo", "Imbabura", "Cotopaxi", "Carchi",
    "Bolívar", "Sucumbíos", "Orellana", "Napo", "Pastaza", "Morona Santiago", "Zamora Chinchipe", "Galápagos",
    "Santo Domingo de los Tsáchilas",
]}


def _es_cedula(valor) -> bool:
    documento, valida = cedula(valor)
    return valida and len(documento) == 10


def _puntajes(valores: list) -> dict:
    """Qué tan probable es que una columna sea cada campo (0 a 1), mirando sus valores."""
    textos = [texto(v) for v in valores]
    n = len(textos)
    if not n:
        return {}
    palabras = [normalizar(t).split() for t in textos]
    es_tarjeta = [any(p in PALABRAS_TARJETA for p in ps) for ps in palabras]
    es_ciudad = [" ".join(ps) in CIUDADES for ps in palabras]
    solo_letras = [bool(ps) and all(p.isalpha() for p in ps) for ps in palabras]
    puntajes = {
        "cedula": sum(_es_cedula(v) for v in valores) / n,
        "numero": sum(bool(telefonos(v)) and not _es_cedula(v) for v in valores) / n,
        "categoria_tarjeta": sum(es_tarjeta) / n,
        "ciudad": sum(es_ciudad) / n,
        "direccion": sum((any(c.isdigit() for c in t) and any(c.isalpha() for c in t)) or any(p in PALABRAS_DIRECCION for p in ps)
                         for t, ps in zip(textos, palabras)) / n,
        "nombre": sum(s and 2 <= len(ps) <= 6 and not c and not k for s, ps, c, k in zip(solo_letras, palabras, es_ciudad, es_tarjeta)) / n,
    }
    # Ciudades que no están en la lista: textos cortos, sin números, que se repiten (los nombres casi nunca se repiten).
    cortos = sum(s and len(ps) <= 3 for s, ps in zip(solo_letras, palabras)) / n
    distintos = len({t.lower() for t in textos})
    if (puntajes["ciudad"] < UMBRAL and cortos >= 0.8 and distintos < n and distintos <= max(3, n * 0.4)
            and puntajes["categoria_tarjeta"] < UMBRAL):
        puntajes["ciudad"] = UMBRAL + 0.1 * (1 - distintos / n)  # cuanto más se repite, más parece ciudad
    return puntajes


def _por_contenido(filas) -> dict:
    """{índice de columna: campo} para un archivo sin encabezados."""
    muestra = [f for f in filas if any(texto(c) for c in f)][:MUESTRA]
    ancho = max((len(f) for f in muestra), default=0)
    puntajes = {j: _puntajes([f[j] for f in muestra if j < len(f) and texto(f[j])]) for j in range(ancho)}
    columnas = {}
    for campo in ["cedula", "numero", "categoria_tarjeta", "ciudad", "direccion", "nombre"]:
        candidatas = [(p.get(campo, 0), j) for j, p in puntajes.items() if j not in columnas and p.get(campo, 0) >= UMBRAL]
        if candidatas:
            columnas[max(candidatas)[1]] = campo
    for j, p in puntajes.items():  # otra columna de teléfonos (celular y convencional)
        if j not in columnas and p.get("numero", 0) >= UMBRAL:
            columnas[j] = "numero"
    if ancho == len(CAMPOS):  # lo que no se pudo deducir se toma del orden del formato de la empresa
        for posicion, campo in enumerate(CAMPOS):
            if campo not in columnas.values() and posicion not in columnas and puntajes.get(posicion):
                columnas[posicion] = campo
    if "numero" not in columnas.values():
        raise ValidationError("No se encontró ninguna columna con números de teléfono. Revise el archivo o use la plantilla "
                              "con el formato de la empresa.")
    return columnas


def _letra(j: int) -> str:
    """0 -> 'A', 26 -> 'AA' (como en Excel)."""
    letras = ""
    j += 1
    while j:
        j, resto = divmod(j - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def formatear(archivo) -> Resultado:
    filas = leer_filas(archivo)
    if len(filas) > MAX_FILAS:
        raise ValidationError(f"El archivo tiene más de {MAX_FILAS:,} filas. Divídalo en partes.".replace(",", "."))
    indice, columnas = _encabezado(filas)
    r = Resultado(fila_encabezado=indice + 1)  # 0 = sin encabezados
    if indice >= 0:
        nombres = {j: texto(filas[indice][j]) for j in columnas}
    else:
        columnas = _por_contenido(filas)
        nombres = {j: f"Columna {_letra(j)}" for j in columnas}
    for j, campo in sorted(columnas.items()):
        r.columnas.setdefault(campo, []).append(nombres[j])
    r.columnas = {c: " + ".join(v) for c, v in r.columnas.items()}

    por_cedula, por_numero = {}, {}  # cédula / teléfono -> fila del archivo donde apareció primero
    for numero_fila, fila in enumerate(filas[indice + 1:], start=indice + 2):
        valores = {}
        for j, campo in columnas.items():
            valor = texto(fila[j]) if j < len(fila) else ""
            if valor:
                valores.setdefault(campo, []).append(fila[j] if campo in ("numero", "cedula") else valor)
        if not valores:
            r.vacias += 1
            continue
        r.leidas += 1
        numeros = telefonos(*valores.get("numero", []))
        if not numeros:
            r.sin_telefono += 1
            continue
        documento, valida = cedula(valores["cedula"][0]) if valores.get("cedula") else ("", True)
        categorias = []
        for c in valores.get("categoria_tarjeta", []):
            if titulo(c) not in categorias:
                categorias.append(titulo(c))
        contacto = {
            "fila": numero_fila,
            "ciudad": titulo(" ".join(valores.get("ciudad", [])))[:100],
            "categoria_tarjeta": " / ".join(categorias)[:150],
            "direccion": titulo(" ".join(valores.get("direccion", [])))[:255],
            "nombre": titulo(" ".join(valores.get("nombre", []) + valores.get("apellido", [])))[:200],
            "cedula": documento, "cedula_valida": valida,
            "numero": numeros[0], "numero_alterno": " / ".join(numeros[1:])[:60],
        }
        # Repetido: la misma persona (cédula válida) o cualquiera de sus teléfonos ya apareció más arriba.
        if documento and valida and documento in por_cedula:
            r.repetidas.append(contacto | {"repite_fila": por_cedula[documento], "criterio": "CEDULA"})
            continue
        if repite := next((por_numero[n] for n in numeros if n in por_numero), None):
            r.repetidas.append(contacto | {"repite_fila": repite, "criterio": "NUMERO"})
            continue
        if documento and valida:
            por_cedula[documento] = numero_fila
        for n in numeros:
            por_numero.setdefault(n, numero_fila)
        if documento and not valida:
            r.cedulas_invalidas += 1
        r.filas.append(contacto)
    if not r.filas:
        raise ValidationError("El archivo no tiene contactos con un número de teléfono válido.")
    return r


# --------------------------------------------------------------------------- Excel con el formato
def excel(registros, con_gestion: bool = False, extra: list | None = None, titulo_hoja: str = "Base") -> bytes:
    """
    La base en el formato de la empresa. Cédula y número como texto, para que Excel no borre el 0.
    `extra`: columnas adicionales al final, como [(encabezado, ancho, función que recibe el registro)].
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    libro = Workbook()
    hoja = libro.active
    hoja.title = titulo_hoja
    extra = extra or []
    encabezados = ENCABEZADOS + (["OTROS NUMEROS", "VENDEDOR", "ESTADO"] if con_gestion else []) + [e[0] for e in extra]
    hoja.append(encabezados)
    for celda in hoja[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="0E325C")
        celda.alignment = Alignment(vertical="center")
    for r in registros:
        fila = [r.ciudad, r.categoria_tarjeta, r.direccion, r.nombre, r.cedula, r.numero]
        if con_gestion:
            fila += [r.numero_alterno, f"{r.vendedor.apellidos} {r.vendedor.nombres}" if r.vendedor_id else "", r.get_estado_display()]
        hoja.append(fila + [obtener(r) for _, _, obtener in extra])
    for columna in ("E", "F", "G") if con_gestion else ("E", "F"):  # cédula, número y otros números como texto
        for celda in hoja[columna][1:]:
            celda.number_format = "@"
    anchos = [16, 20, 38, 32, 14, 13] + ([22, 28, 14] if con_gestion else []) + [e[1] for e in extra]
    for i, ancho in enumerate(anchos, start=1):
        hoja.column_dimensions[get_column_letter(i)].width = ancho
    hoja.freeze_panes = "A2"
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()
