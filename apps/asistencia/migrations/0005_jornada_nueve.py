"""Regla de la empresa: todos entran a las 9:00, sin minutos de tolerancia (las multas empiezan a las 9:00:01)."""
from datetime import time

from django.db import migrations


def aplicar(apps, schema_editor):
    Horario = apps.get_model("asistencia", "Horario")
    Horario.objects.filter(nombre="Jornada estándar").update(hora_entrada=time(9, 0), minutos_tolerancia=0)


def revertir(apps, schema_editor):
    Horario = apps.get_model("asistencia", "Horario")
    Horario.objects.filter(nombre="Jornada estándar").update(hora_entrada=time(8, 0), minutos_tolerancia=5)


class Migration(migrations.Migration):
    dependencies = [("asistencia", "0004_justificada_con_soporte")]

    operations = [migrations.RunPython(aplicar, revertir)]
