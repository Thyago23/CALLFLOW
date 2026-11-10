"""
Busca contactos repetidos (misma cédula o mismo teléfono) en todas las bases comerciales y pasa
a la base de repetidos los que aún no se enviaron a un vendedor; avisa a administradores y
encargados de cierre. Lo mismo que el botón «Buscar repetidos en todas las bases».
Al cargar una base desde Excel ya se revisa sola; esto sirve para las bases cargadas antes o
para programarlo (por ejemplo, cada noche en el Programador de tareas de Windows):

    python manage.py buscar_repetidos
"""
from django.core.management.base import BaseCommand

from apps.ventas import servicios


class Command(BaseCommand):
    help = "Pasa a la base de repetidos los contactos repetidos entre bases comerciales y avisa a los encargados."

    def handle(self, *args, **options):
        r = servicios.buscar_repetidos()
        if options["verbosity"] > 0:
            self.stdout.write(f"Repetidos movidos: {r['movidos']} · ya enviados a vendedores (no se movieron): {r['ya_enviados']}")
