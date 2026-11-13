"""
Salida de empleados (despedido / renunció): el empleado no se borra, queda como ex empleado con su motivo.
Los que ya estaban desvinculados antes de este cambio quedan con motivo «Otro» (se puede corregir desde su ficha).
"""

from django.conf import settings
from django.db import migrations, models


def motivo_para_desvinculados(apps, schema_editor):
    apps.get_model("personal", "Empleado").objects.filter(estado="DESVINCULADO", motivo_salida="").update(motivo_salida="OTRO")


class Migration(migrations.Migration):

    dependencies = [
        ('personal', '0004_cargo_ti'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='empleado',
            name='detalle_salida',
            field=models.TextField(blank=True, default='', help_text='Nota interna sobre la salida (opcional).'),
        ),
        migrations.AddField(
            model_name='empleado',
            name='motivo_salida',
            field=models.CharField(blank=True, choices=[('RENUNCIA', 'Renunció'), ('DESPIDO', 'Despedido'), ('FIN_CONTRATO', 'Fin de contrato'), ('OTRO', 'Otro motivo')], default='', max_length=12),
        ),
        migrations.RunPython(motivo_para_desvinculados, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='empleado',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('estado', 'DESVINCULADO'), ('motivo_salida__in', ['RENUNCIA', 'DESPIDO', 'FIN_CONTRATO', 'OTRO'])), models.Q(models.Q(('estado', 'DESVINCULADO'), _negated=True), ('motivo_salida', '')), _connector='OR'), name='empleado_motivo_salida_coherente'),
        ),
    ]
