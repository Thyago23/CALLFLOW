"""
Genera el código de acceso de las cuentas fijas del departamento de TI.

    python manage.py credenciales_ti                      # las dos cuentas, muestra los códigos en pantalla
    python manage.py credenciales_ti santiago.cedeno      # solo una
    python manage.py credenciales_ti --archivo ruta.txt   # los guarda en un archivo en vez de mostrarlos

El código anterior deja de funcionar. Las cuentas de TI no se gestionan desde «Usuarios» del administrador.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.codigos import generar_codigo_acceso
from apps.core.models import CUENTAS_TI, Usuario


class Command(BaseCommand):
    help = "Genera el código de acceso de las cuentas fijas de TI (santiago.cedeno, nicolas.correa)."

    def add_arguments(self, parser):
        parser.add_argument("usuarios", nargs="*", help="Usuario(s) de TI. Si no se indica, todos.")
        parser.add_argument("--archivo", help="Guardar los códigos en este archivo en lugar de mostrarlos.")

    @transaction.atomic
    def handle(self, *args, usuarios, archivo=None, **options):
        validos = [c[0] for c in CUENTAS_TI]
        elegidos = usuarios or validos
        desconocidos = [u for u in elegidos if u not in validos]
        if desconocidos:
            raise CommandError(f"No son cuentas de TI: {', '.join(desconocidos)}. Las cuentas de TI son: {', '.join(validos)}.")

        lineas = [f"Credenciales de TI de CALLFLOW · generadas el {timezone.localtime():%d/%m/%Y %H:%M}", ""]
        for nombre in elegidos:
            usuario = Usuario.objects.filter(username=nombre, protegida=True).first()
            if usuario is None:
                raise CommandError(f"La cuenta {nombre} no existe: ejecute `python manage.py migrate` e `inicializar_datos`.")
            codigo = generar_codigo_acceso(usuario.first_name, usuario.last_name)
            usuario.set_password(codigo)
            usuario.debe_cambiar_clave = False
            usuario.save(update_fields=["password", "debe_cambiar_clave"])
            lineas.append(f"{usuario.get_full_name():<20} usuario: {usuario.username:<18} código de acceso: {codigo}")
        lineas += ["", "Entregue cada código solo a su dueño. El código anterior ya no funciona."]

        texto = "\n".join(lineas) + "\n"
        if archivo:
            ruta = Path(archivo)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(texto, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Códigos generados para {len(elegidos)} cuenta(s) y guardados en {ruta}."))
        else:
            self.stdout.write(texto)
