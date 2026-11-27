"""Cargo «Contador»: para registrar en Personal a quien lleva la contabilidad de Multivisas y T&T (rol Contador)."""
from django.db import migrations

CARGO = "Contador"


def crear_cargo(apps, schema_editor):
    apps.get_model("personal", "Cargo").objects.get_or_create(
        nombre=CARGO, defaults={"descripcion": "Contabilidad de Multivisas y T&T: caja y bancos, libro diario, nómina y caja chica."})


def quitar_cargo(apps, schema_editor):
    # Solo si nadie lo usa (empleado.cargo es PROTECT).
    apps.get_model("personal", "Cargo").objects.filter(nombre=CARGO, empleados__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [("personal", "0006_motivo_otro")]

    operations = [migrations.RunPython(crear_cargo, quitar_cargo)]
