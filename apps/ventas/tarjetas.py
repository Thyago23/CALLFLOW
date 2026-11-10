"""
Validación y cifrado de tarjetas del DAMI.

- Se valida la marca (según los primeros dígitos, tabla MARCAS), el largo, el dígito de
  control (algoritmo de Luhn) y que la tarjeta no esté caducada.
- El banco emisor se identifica con el catálogo de BIN (modelo RangoBIN, ver bin.py).
- Nada de esto consulta al banco: confirma que el número está bien formado, no que la
  tarjeta exista, esté activa o tenga cupo. Eso solo lo dice la autorización del cobro.
- El número se cifra con Fernet (AES + HMAC) usando CLAVE_TARJETAS del .env.
  Si esa clave se pierde, los números guardados no se pueden recuperar.
"""
import re
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

@dataclass(frozen=True)
class Marca:
    nombre: str
    largos: tuple[int, ...]
    rangos: tuple[tuple[str, str], ...]  # (desde, hasta) de prefijos del mismo largo; el número debe empezar dentro

    def corresponde(self, numero: str) -> int:
        """Largo del prefijo que coincide (0 si no corresponde). Gana el prefijo más largo."""
        mejor = 0
        for desde, hasta in self.rangos:
            inicio = numero[:len(desde)]
            if len(inicio) == len(desde) and desde <= inicio <= hasta:
                mejor = max(mejor, len(desde))
        return mejor


def _r(*prefijos) -> tuple:
    """'34' -> ('34','34'); '51-55' -> ('51','55')."""
    return tuple(tuple(p.split("-")) if "-" in p else (p, p) for p in prefijos)


# Rangos de IIN públicos de cada red (ISO/IEC 7812). El orden no importa: gana el prefijo más largo,
# así 6011 (Discover) le gana a 6 y 622126 (Discover) le gana a 62 (UnionPay).
MARCAS: dict[str, Marca] = {
    "VISA": Marca("Visa", (13, 16, 19), _r("4")),
    "MASTERCARD": Marca("Mastercard", (16,), _r("51-55", "2221-2720")),
    "MAESTRO": Marca("Maestro", tuple(range(12, 20)), _r("5018", "5020", "5038", "5893", "6304", "6759", "6761-6763")),
    "AMEX": Marca("American Express", (15,), _r("34", "37")),
    "DINERS": Marca("Diners Club", (14, 16), _r("300-305", "3095", "36", "38-39")),
    "DISCOVER": Marca("Discover", (16, 17, 18, 19), _r("6011", "644-649", "65", "622126-622925")),
    "JCB": Marca("JCB", (16, 17, 18, 19), _r("3528-3589")),
    "UNIONPAY": Marca("UnionPay", (16, 17, 18, 19), _r("62")),
}


def detectar_marca(numero: str) -> str | None:
    """Código de la marca según los primeros dígitos (sirve con el número a medio escribir)."""
    numero = solo_digitos(numero)
    puntaje = {codigo: m.corresponde(numero) for codigo, m in MARCAS.items()}
    codigo = max(puntaje, key=puntaje.get)
    return codigo if puntaje[codigo] else None


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
    m = MARCAS[marca]
    if len(numero) not in m.largos:
        raise ValidationError(f"Una tarjeta {m.nombre} tiene {largos_texto(m.largos)} dígitos (se escribieron {len(numero)}).")
    if detectar_marca(numero) != marca:
        raise ValidationError(f"Ese número no corresponde a una tarjeta {m.nombre}. Revise la marca marcada.")
    if not luhn_valido(numero):
        raise ValidationError("El número de tarjeta no es válido. Revise que esté bien escrito.")
    return numero


def largos_texto(largos) -> str:
    largos = sorted(largos)
    if len(largos) > 2 and largos == list(range(largos[0], largos[-1] + 1)):
        return f"de {largos[0]} a {largos[-1]}"
    return " o ".join(map(str, largos))


def validar_caducidad(texto: str, hoy: date | None = None) -> tuple[int, int]:
    """'08/28', '0828' o '08/2028' -> (8, 2028). No acepta tarjetas caducadas."""
    coincide = (re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{2}|\d{4})\s*", texto or "")
                or re.fullmatch(r"\s*(\d{2})(\d{2})\s*", texto or ""))
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
    """Solo a través de cierre.servicios.numero_tarjeta_visible. No usar para mostrar en listas."""
    return _fernet().decrypt(token.encode()).decode()
