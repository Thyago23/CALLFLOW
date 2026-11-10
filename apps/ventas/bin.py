"""
Validación de tarjetas (Ventas → Validar tarjetas).

`analizar()` revisa un número sin guardarlo en ningún lado: marca, largo, dígito de control
(Luhn), caducidad y entidad emisora según el catálogo de BIN. El resultado solo lleva el
número enmascarado (6 primeros y 4 últimos dígitos, lo que permite PCI DSS).

Importante: esto confirma que el número está bien formado y de qué banco es; no consulta
al banco, así que no puede saber si la tarjeta existe, está activa o tiene cupo.
"""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction

from . import tarjetas
from .models import EntidadEmisora, RangoBIN

OK, ERROR, AVISO, PENDIENTE = "ok", "error", "aviso", "pendiente"


@dataclass
class Revision:
    estado: str  # ok | error | aviso | pendiente
    titulo: str
    detalle: str = ""


@dataclass
class Resultado:
    digitos: int = 0
    mascara: str = ""
    marca: str | None = None
    marca_nombre: str = ""
    rango: RangoBIN | None = None
    revisiones: list[Revision] = field(default_factory=list)
    motivo: str = ""  # explicación principal cuando el número no es válido, en palabras simples

    @property
    def completo(self) -> bool:
        return all(r.estado != PENDIENTE for r in self.revisiones)

    @property
    def valida(self) -> bool:
        return self.completo and all(r.estado != ERROR for r in self.revisiones)

    @property
    def veredicto(self) -> str:
        if not self.digitos:
            return "vacio"
        if any(r.estado == ERROR for r in self.revisiones):
            return "invalida"
        return "valida" if self.completo else "incompleta"


def enmascarar(numero: str) -> str:
    if len(numero) < 12:
        return numero[:6] + "•" * max(len(numero) - 6, 0)
    return f"{numero[:6]}{'•' * (len(numero) - 10)}{numero[-4:]}"


def buscar_bin(numero: str) -> RangoBIN | None:
    """El BIN más largo que coincide (8, 7 o 6 dígitos)."""
    candidatos = [numero[:n] for n in (8, 7, 6) if len(numero) >= n]
    rangos = {r.bin: r for r in RangoBIN.objects.filter(bin__in=candidatos).select_related("entidad")}
    return next((rangos[c] for c in candidatos if c in rangos), None)


def analizar(numero: str, caducidad: str = "", hoy: date | None = None) -> Resultado:
    numero = tarjetas.solo_digitos(numero)
    r = Resultado(digitos=len(numero))
    if not numero:
        return r
    if not numero.isdigit():
        r.revisiones.append(Revision(ERROR, "Solo dígitos", "El número de tarjeta lleva únicamente números."))
        return r
    r.mascara = enmascarar(numero)

    # 1. Marca
    r.marca = tarjetas.detectar_marca(numero)
    if r.marca:
        r.marca_nombre = tarjetas.MARCAS[r.marca].nombre
        r.revisiones.append(Revision(OK, "Marca", r.marca_nombre))
    elif len(numero) >= 4:
        r.revisiones.append(Revision(ERROR, "Marca", "Los primeros dígitos no corresponden a ninguna marca conocida."))
        r.motivo = "Los primeros dígitos no son de ninguna tarjeta conocida. Revise cómo empieza el número."
    else:
        r.revisiones.append(Revision(PENDIENTE, "Marca", "Siga escribiendo…"))

    # 2. Largo
    largos = tarjetas.MARCAS[r.marca].largos if r.marca else range(12, 20)
    texto_largos = tarjetas.largos_texto(largos)
    if len(numero) in largos:
        r.revisiones.append(Revision(OK, "Cantidad de dígitos", f"{len(numero)} dígitos"))
    elif len(numero) > max(largos):
        sobran = len(numero) - max(largos)
        r.revisiones.append(Revision(ERROR, "Cantidad de dígitos", f"Tiene {len(numero)}; una {r.marca_nombre or 'tarjeta'} tiene {texto_largos}."))
        r.motivo = (f"Sobra{'n' if sobran > 1 else ''} {sobran} dígito{'s' if sobran > 1 else ''}: una {r.marca_nombre or 'tarjeta'} "
                    f"tiene {texto_largos} dígitos. Compare el número con la tarjeta y borre el que está de más.")
    else:
        r.revisiones.append(Revision(PENDIENTE, "Cantidad de dígitos", f"Lleva {len(numero)} de {texto_largos}."))

    # 3. Dígito de control (Luhn): solo tiene sentido con el largo completo
    if len(numero) in largos:
        if tarjetas.luhn_valido(numero):
            r.revisiones.append(Revision(OK, "Dígito de control", "El número es coherente (algoritmo de Luhn)."))
        else:
            r.revisiones.append(Revision(ERROR, "Dígito de control", "Hay un dígito mal escrito o dos dígitos invertidos."))
            r.motivo = r.motivo or ("Algún dígito está mal copiado o hay dos dígitos cambiados de lugar. "
                                    "Compare el número con la tarjeta, dígito por dígito.")
    elif len(numero) > max(largos):
        r.revisiones.append(Revision(PENDIENTE, "Dígito de control", "Se revisa cuando el número tenga la cantidad correcta de dígitos."))
    else:
        r.revisiones.append(Revision(PENDIENTE, "Dígito de control", "Se revisa al completar el número."))

    # 4. Entidad emisora (catálogo de BIN)
    if len(numero) >= 6:
        r.rango = buscar_bin(numero)
        if r.rango:
            detalle = f"{r.rango.entidad} · {r.rango.get_tipo_tarjeta_display()}"
            if r.marca and r.rango.marca != r.marca:
                r.revisiones.append(Revision(ERROR, "Banco emisor",
                                             f"El BIN {r.rango.bin} es {tarjetas.MARCAS[r.rango.marca].nombre} de {r.rango.entidad}, no {r.marca_nombre}."))
            else:
                r.revisiones.append(Revision(OK, "Banco emisor", detalle))
        else:
            r.revisiones.append(Revision(AVISO, "Banco emisor",
                                         f"El BIN {numero[:6]} no está en el catálogo: pregunte al cliente el banco."))
    else:
        r.revisiones.append(Revision(PENDIENTE, "Banco emisor", "Se identifica con los primeros 6 dígitos."))

    # 5. Caducidad (opcional)
    if caducidad.strip():
        try:
            mes, anio = tarjetas.validar_caducidad(caducidad, hoy)
            r.revisiones.append(Revision(OK, "Caducidad", f"Vigente hasta {mes:02d}/{anio}"))
        except ValidationError as error:
            estado = PENDIENTE if len(re.sub(r"\D", "", caducidad)) < 4 else ERROR
            r.revisiones.append(Revision(estado, "Caducidad", " ".join(error.messages) if estado == ERROR else "Siga escribiendo…"))
    return r


# --------------------------------------------------------------------------- importación de BIN
def _clave(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sin_tildes.lower())


_MARCAS_POR_NOMBRE = {_clave(c): c for c in tarjetas.MARCAS} | {_clave(m.nombre): c for c, m in tarjetas.MARCAS.items()} | {
    "master": "MASTERCARD", "mc": "MASTERCARD", "americanexpress": "AMEX", "diners": "DINERS", "discover": "DISCOVER"}
_TIPOS_POR_NOMBRE = {"credito": "CREDITO", "debito": "DEBITO", "prepago": "PREPAGO", "credit": "CREDITO", "debit": "DEBITO", "prepaid": "PREPAGO"}


@dataclass
class Importacion:
    creados: int = 0
    actualizados: int = 0
    errores: list[str] = field(default_factory=list)


@transaction.atomic
def importar_csv(archivo, usuario) -> Importacion:
    """
    Columnas: bin, entidad, marca, tipo (crédito/débito/prepago; opcional). Separador , o ;.
    La entidad debe existir en el catálogo (se busca sin importar tildes ni mayúsculas).
    """
    contenido = archivo.read()
    texto = contenido.decode("utf-8-sig") if isinstance(contenido, bytes) else contenido
    try:
        dialecto = csv.Sniffer().sniff(texto[:2048], delimiters=",;")
    except csv.Error:
        dialecto = csv.excel
    entidades = {_clave(e.nombre): e for e in EntidadEmisora.objects.all()}
    resultado = Importacion()
    for n, fila in enumerate(csv.reader(io.StringIO(texto), dialecto), start=1):
        fila = [c.strip() for c in fila]
        if not any(fila) or (n == 1 and not fila[0].isdigit()):
            continue  # filas vacías y encabezado
        if len(fila) < 3:
            resultado.errores.append(f"Fila {n}: faltan columnas (bin, entidad, marca).")
            continue
        bin_, entidad, marca = fila[0], entidades.get(_clave(fila[1])), _MARCAS_POR_NOMBRE.get(_clave(fila[2]))
        tipo = _TIPOS_POR_NOMBRE.get(_clave(fila[3]) if len(fila) > 3 else "credito")
        if not re.fullmatch(r"\d{6,8}", bin_):
            resultado.errores.append(f"Fila {n}: el BIN «{bin_}» debe tener de 6 a 8 dígitos.")
        elif entidad is None:
            resultado.errores.append(f"Fila {n}: la entidad «{fila[1]}» no está en el catálogo.")
        elif marca is None:
            resultado.errores.append(f"Fila {n}: marca «{fila[2]}» desconocida.")
        elif tipo is None:
            resultado.errores.append(f"Fila {n}: tipo «{fila[3]}» desconocido (crédito, débito o prepago).")
        elif tarjetas.detectar_marca(bin_) != marca:
            resultado.errores.append(f"Fila {n}: el BIN {bin_} no corresponde a {tarjetas.MARCAS[marca].nombre}.")
        else:
            _, creado = RangoBIN.objects.update_or_create(
                bin=bin_, defaults={"entidad": entidad, "marca": marca, "tipo_tarjeta": tipo, "registrado_por": usuario})
            if creado:
                resultado.creados += 1
            else:
                resultado.actualizados += 1
    return resultado
