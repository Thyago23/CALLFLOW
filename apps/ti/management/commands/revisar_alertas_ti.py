"""Revisa licencias, mantenimientos, respaldos y disco, y avisa a TI de lo que toca (una sola vez cada cosa)."""
from django.core.management.base import BaseCommand

from apps.ti import alertas


class Command(BaseCommand):
    help = "Avisa a TI de licencias por vencer, mantenimientos pendientes, respaldos fallidos y poco espacio en disco."

    def handle(self, *args, **options):
        resultado = alertas.revisar()
        for nombre, enviados in resultado.items():
            texto = "falló (ver TI › Servidor)" if enviados is None else f"{enviados} aviso(s) nuevo(s)"
            self.stdout.write(f"{nombre}: {texto}")
