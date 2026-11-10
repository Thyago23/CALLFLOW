"""
Crea las incidencias «Sin marcación de salida» de quienes no pulsaron «Finalizar día» y avisa
a los administradores. Pensado para el Programador de tareas de Windows (por ejemplo, cada 15 minutos):

    python manage.py revisar_salidas

Aunque no se programe, la revisión también corre sola mientras alguien tenga CALLFLOW abierto.
"""
from django.core.management.base import BaseCommand

from apps.asistencia.servicios import revisar_salidas_pendientes


class Command(BaseCommand):
    help = "Registra las jornadas que quedaron sin hora de salida y notifica a los administradores."

    def handle(self, *args, **options):
        from apps.core import push

        creadas = revisar_salidas_pendientes()
        enviados = push.enviar_pendientes()
        if options["verbosity"] > 0:
            self.stdout.write(f"Jornadas sin salida registradas: {creadas} · mensajes push: {enviados}")
