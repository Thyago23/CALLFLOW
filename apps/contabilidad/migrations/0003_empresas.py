"""Las dos empresas que lleva la contadora. Los datos (razón social, RUC) se completan desde el admin."""
from django.db import migrations

EMPRESAS = [("MULTIVISAS", "Multivisas"), ("TYT", "T&T")]


def crear(apps, schema_editor):
    Empresa = apps.get_model("contabilidad", "Empresa")
    for codigo, nombre in EMPRESAS:
        Empresa.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre})


class Migration(migrations.Migration):
    dependencies = [("contabilidad", "0002_reglas_contabilidad")]

    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
