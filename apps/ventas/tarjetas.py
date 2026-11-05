"""
Validación y cifrado de tarjetas del DAMI.

- Se valida la marca (Visa, Mastercard, American Express, Diners), el largo, el dígito de
  control (algoritmo de Luhn) y que la tarjeta no esté caducada.
- El número se cifra con Fernet (AES + HMAC) usando CLAVE_TARJETAS del .env.
  Si esa clave se pierde, los números guardados no se pueden recuperar.
"""
import re
from datetime import date

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

_REGLAS = {
    "VISA": ((16,), lambda n: n.startswith("4"), "Visa"),
    "MASTERCARD": ((16,), lambda n: 51 <= int(n[:2]) <= 55 or 2221 <= int(n[:4]) <= 2720, "Mastercard"),
    "AMEX": ((15,), lambda n: n[:2] in ("34", "37"), "American Express"),
    "DINERS": ((14, 16), lambda n: n[:2] in ("36", "38", "39") or 300 <= int(n[:3]) <= 305, "Diners"),
}


def solo_digitos(texto: str) -> str:
    return re.sub(r"[\s-]", "", texto or "")


def luhn_valido(numero: str) -> bool:
    suma, alternar = 0, False
    for d in reversed(numero):
        n = int(d)
        if alternar:
            n = n * 2 - 9 if n > 4 else n * 2
        suma += n
        alternar = not alternar
    return suma % 10 == 0


def validar_numero(marca: str, numero: str) -> str:
    numero = solo_digitos(numero)
    if not numero.isdigit():
        raise ValidationError("El número de tarjeta solo debe tener dígitos.")
    largos, prefijo_ok, nombre = _REGLAS[marca]
    if len(numero) not in largos:
        raise ValidationError(f"Una tarjeta {nombre} tiene {' o '.join(map(str, largos))} dígitos (se escribieron {len(numero)}).")
    if not prefijo_ok(numero):
        raise ValidationError(f"Ese número no corresponde a una tarjeta {nombre}. Revise la marca marcada.")
    if not luhn_valido(numero):
        raise ValidationError("El número de tarjeta no es válido. Revise que esté bien escrito.")
    return numero


def validar_caducidad(texto: str, hoy: date | None = None) -> tuple[int, int]:
    """'08/28' o '08/2028' -> (8, 2028). No acepta tarjetas caducadas."""
    coincide = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{2}|\d{4})\s*", texto or "")
    if not coincide:
        raise ValidationError("Escriba la caducidad como MM/AA, por ejemplo 08/28.")
    mes, anio = int(coincide.group(1)), int(coincide.group(2))
    anio += 2000 if anio < 100 else 0
    if not 1 <= mes <= 12:
        raise ValidationError("El mes de caducidad debe estar entre 01 y 12.")
    hoy = hoy or date.today()
    if (anio, mes) < (hoy.year, hoy.month):
        raise ValidationError("La tarjeta está caducada.")
    if anio > hoy.year + 20:
        raise ValidationError("Revise el año de caducidad.")
    return mes, anio


def _fernet():
    from cryptography.fernet import Fernet

    clave = getattr(settings, "CLAVE_TARJETAS", "")
    if not clave or clave == "cambie-esto":
        raise ImproperlyConfigured("Falta CLAVE_TARJETAS en el .env para cifrar los números de tarjeta.")
    return Fernet(clave.encode())


def cifrar(numero: str) -> str:
    return _fernet().encrypt(numero.encode()).decode()


def descifrar(token: str) -> str:
    """Solo para el futuro módulo de cierre (aún sin pantalla). No usar para mostrar en listas."""
    return _fernet().decrypt(token.encode()).decode()
