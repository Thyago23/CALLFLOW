"""
Genera el par de claves VAPID para las notificaciones push del navegador.

    python manage.py generar_claves_push            # las muestra
    python manage.py generar_claves_push --guardar  # además las agrega al .env si aún no están

Cada instalación (desarrollo, producción) usa sus propias claves. Si se cambian, los navegadores
tienen que volver a activar las notificaciones.
"""
import base64
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def nuevas_claves() -> tuple[str, str]:
    """(privada en DER base64url, pública sin comprimir base64url), el formato que usan pywebpush y el navegador."""
    privada = ec.generate_private_key(ec.SECP256R1())
    der = privada.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    publica = privada.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(der), _b64(publica)


class Command(BaseCommand):
    help = "Genera las claves VAPID para las notificaciones push del navegador."

    def add_arguments(self, parser):
        parser.add_argument("--guardar", action="store_true", help="Agregarlas al archivo .env del proyecto.")

    def handle(self, *args, **options):
        privada, publica = nuevas_claves()
        lineas = [f"VAPID_PUBLIC_KEY={publica}", f"VAPID_PRIVATE_KEY={privada}"]
        if not options["guardar"]:
            self.stdout.write("Agregue estas líneas al .env:\n" + "\n".join(lineas))
            return
        ruta = Path(settings.BASE_DIR) / ".env"
        if not ruta.exists():
            raise CommandError("No existe el archivo .env. Cópielo desde .env.example primero.")
        texto = ruta.read_text(encoding="utf-8")
        if re.search(r"^VAPID_PRIVATE_KEY=\S", texto, re.MULTILINE):
            raise CommandError("El .env ya tiene claves VAPID. Bórrelas primero si de verdad quiere cambiarlas.")
        texto = re.sub(r"^VAPID_(PUBLIC|PRIVATE)_KEY=[ \t]*\r?\n?", "", texto, flags=re.MULTILINE)  # las vacías de .env.example
        separador = "" if texto.endswith("\n") else "\n"
        ruta.write_text(texto + separador + "\n# Notificaciones push del navegador\n" + "\n".join(lineas) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS("Claves VAPID guardadas en el .env. Reinicie el servidor para usarlas."))
