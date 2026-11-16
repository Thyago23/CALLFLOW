"""
Falta justificada proporcional al sueldo (aclaración de la empresa del 16/11/2026).

Antes era un monto fijo de $13,33 (un día de un sueldo de $400). Ahora se descuenta un día del
sueldo de cada persona: sueldo ÷ 30. Con $400 sigue siendo $13,33; con $600 son $20,00.
La falta no justificada ya era proporcional (10 % del sueldo) y no cambia.

Se ajusta la versión 2 de las penalizaciones (vigente desde noviembre de 2026, todavía sin
ningún periodo cerrado con ella). La versión 1 (hasta octubre) conserva el monto fijo.
"""
from django.db import migrations

DESCRIPCION_ANTES = "1–9 min $1 · 10–29 min $3 · 30+ min: llamado de atención, $4 y falta. Tope: 10 % del sueldo."
DESCRIPCION = DESCRIPCION_ANTES + " Falta justificada: un día de sueldo (sueldo ÷ 30)."


def aplicar(apps, schema_editor):
    ReglaCalculo = apps.get_model("nomina", "ReglaCalculo")
    v2 = ReglaCalculo.objects.filter(codigo="PENALIZACIONES", version=2).first()
    if v2 is None:
        return
    v2.parametros = {**v2.parametros, "falta_justificada_dias": 1}
    v2.descripcion = DESCRIPCION
    v2.save(update_fields=["parametros", "descripcion"])


def revertir(apps, schema_editor):
    ReglaCalculo = apps.get_model("nomina", "ReglaCalculo")
    v2 = ReglaCalculo.objects.filter(codigo="PENALIZACIONES", version=2).first()
    if v2 is None:
        return
    v2.parametros = {k: v for k, v in v2.parametros.items() if k != "falta_justificada_dias"}
    v2.descripcion = DESCRIPCION_ANTES
    v2.save(update_fields=["parametros", "descripcion"])


class Migration(migrations.Migration):
    dependencies = [("nomina", "0012_penalizaciones_v2")]

    operations = [migrations.RunPython(aplicar, revertir)]
