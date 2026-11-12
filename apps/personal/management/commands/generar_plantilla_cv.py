"""
Genera la plantilla de hoja de vida para llenar (PDF) que entiende el lector de CV del sistema.

    python manage.py generar_plantilla_cv                 # static/documentos/hoja-de-vida-multivisa.pdf
    python manage.py generar_plantilla_cv --salida x.pdf
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.personal import plantilla_cv

DESTINO = Path(settings.BASE_DIR) / "static" / "documentos" / "hoja-de-vida-multivisa.pdf"


class Command(BaseCommand):
    help = "Genera la plantilla de hoja de vida (PDF para llenar) que lee el sistema al registrar un empleado."

    def add_arguments(self, parser):
        parser.add_argument("--salida", help=f"Ruta del PDF (por defecto {DESTINO}).")

    def handle(self, *args, salida=None, **opciones):
        ruta = Path(salida) if salida else DESTINO
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(plantilla_cv.generar())
        self.stdout.write(self.style.SUCCESS(f"Plantilla de hoja de vida guardada en {ruta}."))
