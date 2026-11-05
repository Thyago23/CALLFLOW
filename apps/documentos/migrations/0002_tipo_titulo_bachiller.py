"""Tipo de documento 'Título de bachiller', exigido en el expediente de ingreso junto al CV y la cédula."""
from django.db import migrations


def crear(apps, schema_editor):
    TipoDocumento = apps.get_model("documentos", "TipoDocumento")
    TipoDocumento.objects.update_or_create(
        codigo="TITULO_BACHILLER", defaults={"nombre": "Título de bachiller", "requerido_ingreso": True, "activo": True}
    )


def quitar(apps, schema_editor):
    TipoDocumento = apps.get_model("documentos", "TipoDocumento")
    TipoDocumento.objects.filter(codigo="TITULO_BACHILLER", documentos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("documentos", "0001_initial")]

    operations = [migrations.RunPython(crear, quitar)]
