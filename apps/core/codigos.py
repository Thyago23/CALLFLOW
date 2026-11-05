"""
Credenciales de CALLFLOW.

- Usuario: nombre.apellido en minúsculas y sin tildes (lucia.mora). Si ya existe: lucia.mora2, lucia.mora3…
- Código de acceso (la contraseña): iniciales + 8 dígitos aleatorios (LM48207153).
  Es aleatorio a propósito: no se puede deducir de la cédula ni de otro dato personal.
"""
import re
import secrets
import unicodedata

from django.db import connection

LARGO_DIGITOS = 8


def _ascii(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()


def inicial(texto: str) -> str:
    letras = re.sub(r"[^A-Z]", "", _ascii(texto).strip().upper())
    return letras[:1] or "X"


def prefijo_de(nombres: str, apellidos: str) -> str:
    return inicial(nombres) + inicial(apellidos)


def normalizar_usuario(texto: str) -> str:
    """'  Lucía   Mora ' / 'LUCIA.MORA' / 'lucia_mora' -> 'lucia.mora'."""
    limpio = re.sub(r"[^a-z0-9]+", ".", _ascii(texto).lower())
    return limpio.strip(".")


def base_usuario(nombres: str, apellidos: str) -> str:
    """Primer nombre + primer apellido: 'María José', 'Pérez Ñañez' -> 'maria.perez'."""
    primero = normalizar_usuario((nombres or "").split()[0] if (nombres or "").split() else "") or "usuario"
    apellido = normalizar_usuario((apellidos or "").split()[0] if (apellidos or "").split() else "")
    return f"{primero}.{apellido}" if apellido else primero


def siguiente_usuario(base: str, existentes) -> str:
    usados = set(existentes)
    if base not in usados:
        return base
    n = 2
    while f"{base}{n}" in usados:
        n += 1
    return f"{base}{n}"


def generar_usuario(nombres: str, apellidos: str) -> str:
    """Debe llamarse dentro de una transacción (bloqueo para evitar dos usuarios iguales a la vez)."""
    from .models import Usuario

    base = base_usuario(nombres, apellidos)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"usuario_{base}"])
    existentes = Usuario.objects.filter(username__regex=rf"^{re.escape(base)}[0-9]*$").values_list("username", flat=True)
    return siguiente_usuario(base, existentes)


def generar_codigo_acceso(nombres: str, apellidos: str) -> str:
    """Iniciales + 8 dígitos aleatorios criptográficamente seguros, ej. LM48207153."""
    digitos = "".join(secrets.choice("0123456789") for _ in range(LARGO_DIGITOS))
    return prefijo_de(nombres, apellidos) + digitos


# --- Usado por la migración core/0005 (formato anterior de códigos). No eliminar. ---
PATRON_CODIGO = re.compile(r"^[A-Z]{2}\d{3,}$")


def siguiente_codigo(prefijo: str, existentes) -> str:
    numeros = [int(c[len(prefijo):]) for c in existentes if re.fullmatch(rf"{prefijo}\d+", c)]
    return f"{prefijo}{max(numeros, default=0) + 1:03d}"
