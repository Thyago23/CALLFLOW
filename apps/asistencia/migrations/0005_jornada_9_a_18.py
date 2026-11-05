from datetime import time

from django.db import migrations


def jornada_9_a_18(apps, schema_editor):
    """La jornada de la empresa es de 09:00 a 18:00. Solo cambia la jornada estándar si conserva el horario anterior."""
    Horario = apps.get_model("asistencia", "Horario")
    Horario.objects.filter(nombre="Jornada estándar", hora_entrada=time(8, 0), hora_salida=time(17, 0)).update(
        hora_entrada=time(9, 0), hora_salida=time(18, 0))


def volver_8_a_17(apps, schema_editor):
    Horario = apps.get_model("asistencia", "Horario")
    Horario.objects.filter(nombre="Jornada estándar", hora_entrada=time(9, 0), hora_salida=time(18, 0)).update(
        hora_entrada=time(8, 0), hora_salida=time(17, 0))


class Migration(migrations.Migration):
    dependencies = [("asistencia", "0004_justificada_con_soporte")]

    operations = [migrations.RunPython(jornada_9_a_18, volver_8_a_17)]
