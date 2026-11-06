"""
Une las dos ramas de la jornada: 0005_jornada_9_a_18 (09:00 a 18:00) y 0005_jornada_nueve
(09:00 sin tolerancia). Según el orden en que corran, la salida podía quedar a las 17:00;
aquí se deja el resultado final explícito: 09:00 a 18:00, sin minutos de tolerancia.
"""
from datetime import time

from django.db import migrations


def jornada_final(apps, schema_editor):
    Horario = apps.get_model("asistencia", "Horario")
    Horario.objects.filter(nombre="Jornada estándar", hora_salida__in=[time(17, 0), time(18, 0)]).update(
        hora_entrada=time(9, 0), hora_salida=time(18, 0), minutos_tolerancia=0)


class Migration(migrations.Migration):
    dependencies = [
        ("asistencia", "0005_jornada_9_a_18"),
        ("asistencia", "0005_jornada_nueve"),
    ]

    operations = [migrations.RunPython(jornada_final, migrations.RunPython.noop)]
