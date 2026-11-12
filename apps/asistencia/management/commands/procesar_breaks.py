"""
Envía los recordatorios y empieza los breaks programados que ya llegaron a su hora.

Normalmente no hace falta: lo hace la campana de cada navegador abierto (cada 5 s). Sirve para
un cron o una tarea programada de Windows cada minuto, si se quiere que ocurra aunque nadie tenga
CALLFLOW abierto:  python manage.py procesar_breaks
"""
from django.core.management.base import BaseCommand

from apps.asistencia.breaks import procesar_programados


class Command(BaseCommand):
    help = "Recordatorios e inicio de los breaks programados."

    def handle(self, *args, **options):
        cambios = procesar_programados()
        self.stdout.write(f"Breaks programados procesados: {cambios}")
