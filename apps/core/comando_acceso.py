"""Base de los comandos que dan o quitan un permiso exclusivo (acceso_chips, acceso_breaks)."""
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError

from .models import Usuario


class ComandoAccesoExclusivo(BaseCommand):
    app_label = ""
    codename = ""
    nombre = ""  # «al registro de chips»

    def add_arguments(self, parser):
        parser.add_argument("usuario", nargs="?")
        parser.add_argument("--quitar", action="store_true")
        parser.add_argument("--ver", action="store_true")

    def handle(self, *args, usuario=None, quitar=False, ver=False, **opciones):
        permiso = Permission.objects.get(content_type__app_label=self.app_label, codename=self.codename)
        if ver or not usuario:
            con_acceso = Usuario.objects.filter(user_permissions=permiso).order_by("username")
            self.stdout.write(f"Con acceso {self.nombre}: " + (", ".join(u.username for u in con_acceso) or "nadie"))
            return
        try:
            cuenta = Usuario.objects.get(username=usuario)
        except Usuario.DoesNotExist as error:
            raise CommandError(f"No existe el usuario {usuario}.") from error
        if quitar:
            cuenta.user_permissions.remove(permiso)
            self.stdout.write(self.style.SUCCESS(f"{cuenta} ya no tiene acceso {self.nombre}."))
        else:
            cuenta.user_permissions.add(permiso)
            self.stdout.write(self.style.SUCCESS(f"{cuenta} tiene acceso {self.nombre}."))
