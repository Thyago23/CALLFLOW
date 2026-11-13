"""
Flujo de cierre en etapas: filtro (Javier) -> gestión (Marlon) -> cierre final (Javier).
Los DAMI que ya estaban «en gestión» con la cola compartida quedan en cierre final: los cierra quien los tenía.
"""

from django.db import migrations, models


def etapa_de_los_tomados(apps, schema_editor):
    apps.get_model("ventas", "FormularioDAMI").objects.filter(estado="EN_GESTION").update(etapa="CIERRE")


class Migration(migrations.Migration):

    dependencies = [
        ('personal', '0006_motivo_otro'),
        ('ventas', '0013_tarjeta_no_entregada'),
    ]

    operations = [
        migrations.AddField(
            model_name='formulariodami',
            name='etapa',
            field=models.CharField(choices=[('FILTRO', 'Filtro'), ('GESTION', 'Gestión'), ('CIERRE', 'Cierre final')], default='FILTRO', help_text='En qué parte del cierre está: filtro, gestión o cierre final. Al terminar queda la última.', max_length=8),
        ),
        migrations.RunPython(etapa_de_los_tomados, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='formulariodami',
            constraint=models.CheckConstraint(condition=models.Q(('etapa__in', ['FILTRO', 'GESTION', 'CIERRE'])), name='dami_etapa_valida'),
        ),
        migrations.AddConstraint(
            model_name='formulariodami',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('estado', 'ENVIADO'), _negated=True), models.Q(('encargado_asignado__isnull', True), ('etapa', 'FILTRO')), _connector='OR'), name='dami_enviado_en_filtro'),
        ),
        migrations.AddConstraint(
            model_name='formulariodami',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('estado', 'EN_GESTION'), _negated=True), ('encargado_asignado__isnull', False), _connector='OR'), name='dami_en_gestion_con_encargado'),
        ),
    ]
