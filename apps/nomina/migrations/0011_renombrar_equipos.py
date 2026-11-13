"""
Renombra los equipos de vendedores: «Baymont Travel» → «Paquetería» y «Visas» → «Visado».
Los tramos de comisión y las asignaciones no cambian (solo el nombre del esquema).
"""
from django.db import migrations

CAMBIOS = {"Baymont Travel": "Paquetería", "Visas": "Visado"}


def renombrar(apps, schema_editor, cambios):
    EsquemaComision = apps.get_model("personal", "EsquemaComision")
    for anterior, nuevo in cambios.items():
        if not EsquemaComision.objects.filter(nombre=nuevo).exists():
            EsquemaComision.objects.filter(nombre=anterior).update(nombre=nuevo)


def adelante(apps, schema_editor):
    renombrar(apps, schema_editor, CAMBIOS)


def atras(apps, schema_editor):
    renombrar(apps, schema_editor, {nuevo: anterior for anterior, nuevo in CAMBIOS.items()})


class Migration(migrations.Migration):
    dependencies = [
        ("nomina", "0010_reglas_recibos_caja"),
    ]

    operations = [migrations.RunPython(adelante, atras)]
