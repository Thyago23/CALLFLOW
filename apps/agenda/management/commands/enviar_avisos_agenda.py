"""
Envía los avisos de agenda pendientes a todas las personas, aunque no tengan sesión abierta.
Pensado para el Programador de tareas de Windows, cada minuto:

    python manage.py enviar_avisos_agenda
"""
from django.core.management.base import BaseCommand

from apps.agenda.servicios import generar_avisos


class Command(BaseCommand):
    help = "Crea las notificaciones de los eventos de agenda cuyo aviso ya llegó."

    def handle(self, *args, **options):
        total = generar_avisos()
        if options["verbosity"] > 0:
            self.stdout.write(f"Avisos de agenda enviados: {total}")
