"""
Entidades del sistema financiero del Ecuador que emiten tarjetas de crédito o débito.
El catálogo se puede ampliar desde Ventas → Validar tarjetas → Catálogo.
"""
from django.db import migrations

from apps.core.sql import auditar

BANCOS = [
    "Banco Pichincha", "Banco Guayaquil", "Banco del Pacífico", "Produbanco", "Banco Internacional",
    "Banco Bolivariano", "Banco Diners Club del Ecuador", "Banco del Austro", "Banco General Rumiñahui",
    "Banco de Machala", "Banco de Loja", "Banco Solidario", "Banco ProCredit", "Banco Amazonas",
    "Banco Codesarrollo", "Banco Coopnacional", "Banco Capital", "Banco Comercial de Manabí",
    "Banco del Litoral", "Banco D-MIRO", "Banco Finca", "Banco VisionFund Ecuador", "Banco Delbank",
    "Citibank Ecuador", "BanEcuador",
]
COOPERATIVAS = [
    "Cooperativa JEP", "Cooperativa Jardín Azuayo", "Cooperativa Policía Nacional", "Cooperativa 29 de Octubre",
    "Cooperativa Alianza del Valle", "Cooperativa Cooprogreso", "Cooperativa Andalucía", "Cooperativa Oscus",
    "Cooperativa San Francisco", "Cooperativa Mushuc Runa", "Cooperativa Riobamba", "Cooperativa Atuntaqui",
]
MUTUALISTAS = ["Mutualista Pichincha", "Mutualista Azuay"]


def cargar(apps, schema_editor):
    Entidad = apps.get_model("ventas", "EntidadEmisora")
    for tipo, nombres in (("BANCO", BANCOS), ("COOPERATIVA", COOPERATIVAS), ("MUTUALISTA", MUTUALISTAS)):
        for nombre in nombres:
            Entidad.objects.get_or_create(nombre=nombre, defaults={"tipo": tipo})


class Migration(migrations.Migration):
    dependencies = [("ventas", "0008_catalogo_bancos_y_bin"), ("core", "0002_auditoria")]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
        migrations.RunSQL(*auditar("ventas_rangobin")),
    ]
