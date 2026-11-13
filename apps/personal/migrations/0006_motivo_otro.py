"""«Otro motivo» de salida: se escribe cuál fue. Los ya registrados con «Otro» quedan como «Sin especificar»."""

from django.conf import settings
from django.db import migrations, models


def texto_para_otros(apps, schema_editor):
    apps.get_model("personal", "Empleado").objects.filter(motivo_salida="OTRO", motivo_otro="").update(motivo_otro="Sin especificar")


class Migration(migrations.Migration):

    dependencies = [
        ('personal', '0005_salida_empleado'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='empleado',
            name='motivo_otro',
            field=models.CharField(blank=True, default='', help_text='Cuál fue el motivo, si es «Otro motivo».', max_length=120),
        ),
        migrations.RunPython(texto_para_otros, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='empleado',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('motivo_salida', 'OTRO'), models.Q(('motivo_otro', ''), _negated=True)), models.Q(models.Q(('motivo_salida', 'OTRO'), _negated=True), ('motivo_otro', '')), _connector='OR'), name='empleado_motivo_otro_coherente'),
        ),
    ]
