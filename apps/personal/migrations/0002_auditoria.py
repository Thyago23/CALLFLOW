from django.db import migrations

from apps.core.sql import auditar

TABLAS = [
    "personal_empleado",
    "personal_historialsalario",
    "personal_esquemacomision",
    "personal_tramocomision",
    "personal_asignacioncomision",
]


class Migration(migrations.Migration):
    dependencies = [
        ("personal", "0001_initial"),
        ("core", "0002_auditoria"),
    ]

    operations = [migrations.RunSQL(*auditar(tabla)) for tabla in TABLAS]
