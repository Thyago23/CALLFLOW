"""
Envía como push las notificaciones que aún no salieron (por ejemplo, las de stock bajo creadas
por la base de datos). El sistema ya las envía solo al usarse; esto cubre los ratos sin uso.
Para el Programador de tareas de Windows, cada minuto:

    python manage.py enviar_push
"""
from django.core.management.base import BaseCommand

from apps.core import push


class Command(BaseCommand):
    help = "Envía como push del navegador las notificaciones pendientes."

    def handle(self, *args, **options):
        if not push.configurado():
            self.stdout.write("Push sin configurar: ejecute python manage.py generar_claves_push --guardar")
            return
        total = push.enviar_pendientes()
        if options["verbosity"] > 0:
            self.stdout.write(f"Mensajes push enviados: {total}")
