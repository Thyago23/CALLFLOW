"""Cargo «Tecnología (TI)»: sin él, el personal del departamento de TI no se podía registrar en Personal."""
from django.db import migrations

CARGO = "Tecnología (TI)"


def crear_cargo(apps, schema_editor):
    apps.get_model("personal", "Cargo").objects.get_or_create(
        nombre=CARGO, defaults={"descripcion": "Departamento de TI: equipos, soporte y sistemas."})


def quitar_cargo(apps, schema_editor):
    # Solo si nadie lo usa (empleado.cargo es PROTECT).
    apps.get_model("personal", "Cargo").objects.filter(nombre=CARGO, empleados__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [("personal", "0003_observaciones")]

    operations = [migrations.RunPython(crear_cargo, quitar_cargo)]
