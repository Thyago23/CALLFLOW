"""Validaciones reutilizables (identificaciones de Ecuador, teléfonos, nombres)."""
import re

from django.core.exceptions import ValidationError

_PROVINCIAS_VALIDAS = set(range(1, 25)) | {30}  # 01–24 y 30 (ecuatorianos registrados en el exterior)


def cedula_valida(cedula: str) -> bool:
    """Cédula ecuatoriana: 10 dígitos, código de provincia válido y dígito verificador (módulo 10)."""
    if not re.fullmatch(r"\d{10}", cedula or ""):
        return False
    if int(cedula[:2]) not in _PROVINCIAS_VALIDAS or int(cedula[2]) >= 6:
        return False
    suma = 0
    for i, coeficiente in enumerate((2, 1, 2, 1, 2, 1, 2, 1, 2)):
        producto = int(cedula[i]) * coeficiente
        suma += producto - 9 if producto > 9 else producto
    return (10 - suma % 10) % 10 == int(cedula[9])


def ruc_persona_natural_valido(ruc: str) -> bool:
    """RUC de persona natural: cédula válida + establecimiento distinto de 000."""
    return bool(re.fullmatch(r"\d{13}", ruc or "")) and cedula_valida(ruc[:10]) and ruc[10:] != "000"


def normalizar_identificacion(valor: str) -> str:
    return re.sub(r"[\s.\-]", "", valor or "").upper()


def validar_identificacion(tipo: str, valor: str) -> None:
    if tipo == "CEDULA" and not cedula_valida(valor):
        raise ValidationError("La cédula no es válida. Revise que tenga 10 dígitos y que estén bien escritos.", code="cedula")
    if tipo == "RUC" and not ruc_persona_natural_valido(valor):
        raise ValidationError("El RUC no es válido. Debe tener 13 dígitos (cédula + 001).", code="ruc")
    if tipo == "PASAPORTE" and not re.fullmatch(r"[A-Z0-9]{5,20}", valor):
        raise ValidationError("El pasaporte debe tener entre 5 y 20 letras o números.", code="pasaporte")


def normalizar_telefono(valor: str) -> str:
    return re.sub(r"[\s\-().]", "", valor or "")


def validar_telefono(valor: str) -> None:
    if not re.fullmatch(r"\+?\d{7,15}", valor):
        raise ValidationError("Ingrese un teléfono válido (solo números, entre 7 y 15 dígitos).", code="telefono")


def normalizar_nombre(valor: str) -> str:
    """'  maría   josé ' -> 'María José'. Respeta tildes, ñ, apóstrofos y guiones."""
    limpio = re.sub(r"\s+", " ", valor or "").strip()
    return " ".join(p[:1].upper() + p[1:].lower() for p in limpio.split(" "))


def validar_nombre(valor: str) -> None:
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' \-]{2,100}", valor):
        raise ValidationError("Use solo letras (se permiten tildes, ñ, espacios, apóstrofos y guiones).", code="nombre")


# --------------------------------------------------------------------------- ids que llegan en la petición
def entero(valor) -> int | None:
    """Un id o número que llega por GET/POST, o None si no es un entero positivo (nunca lanza error)."""
    texto = str(valor or "").strip()
    return int(texto) if texto.isdigit() and len(texto) <= 18 else None


def enteros(valores) -> list[int]:
    """Lista de ids (getlist): descarta lo que no sea un entero positivo."""
    return [n for n in (entero(v) for v in valores or []) if n is not None]
