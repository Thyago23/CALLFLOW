"""
Reglas de nómina aprobadas por la empresa (SCRUM-14).

Equipos y comisiones (sobre el total de ventas cerradas del mes; el porcentaje del tramo
alcanzado se aplica a TODO el total y se suma al sueldo base):
  Visas:          hasta 1.300 → 0 %  ·  más de 1.300 → 11 %  ·  más de 3.000 → 15 %
  Baymont Travel: hasta 3.000 → 0 %  ·  más de 3.000 → 11 %  ·  más de 5.000 → 15 %

Penalizaciones (entrada a las 9:00):
  falta no justificada → 10 % del sueldo base · falta justificada → $13,33
  atraso de 1 minuto (9:00:01–9:01) → $1 · hasta 10 minutos (9:10) → $5 · más → llamado de atención
  3 llamados de atención → visto bueno
"""
from datetime import date
from decimal import Decimal

from django.db import migrations

ESQUEMAS = {
    "Visas": [(Decimal("0"), Decimal("1300"), Decimal("0")), (Decimal("1300"), Decimal("3000"), Decimal("11")), (Decimal("3000"), None, Decimal("15"))],
    "Baymont Travel": [(Decimal("0"), Decimal("3000"), Decimal("0")), (Decimal("3000"), Decimal("5000"), Decimal("11")), (Decimal("5000"), None, Decimal("15"))],
}

REGLAS = [
    ("SUELDO_BASE", "Sueldo base por defecto", "Sueldo con el que se registra un empleado nuevo; luego se edita por persona.",
     {"monto": "400.00"}),
    ("PENALIZACIONES", "Penalizaciones por faltas y atrasos", "Descuentos y multas de asistencia. Hora de entrada 9:00.", {
        "hora_entrada": "09:00",
        "falta_no_justificada_porcentaje": "10",
        "falta_justificada_monto": "13.33",
        "atraso_multas": [{"hasta_minutos": 1, "multa": "1.00"}, {"hasta_minutos": 10, "multa": "5.00"}],
        "llamados_para_visto_bueno": 3,
        "ventana_llamados_dias": 365,
        "aviso_multas_porcentaje": "10",
    }),
]


def crear(apps, schema_editor):
    EsquemaComision = apps.get_model("personal", "EsquemaComision")
    TramoComision = apps.get_model("personal", "TramoComision")
    ReglaCalculo = apps.get_model("nomina", "ReglaCalculo")
    for nombre, tramos in ESQUEMAS.items():
        esquema, creado = EsquemaComision.objects.get_or_create(
            nombre=nombre, defaults={"tipo": "ESCALONADO", "descripcion": "El porcentaje del tramo alcanzado se aplica a todo el total vendido en el mes."},
        )
        if creado:
            for desde, hasta, porcentaje in tramos:
                TramoComision.objects.create(esquema=esquema, desde_monto=desde, hasta_monto=hasta, porcentaje=porcentaje)
    for codigo, nombre, descripcion, parametros in REGLAS:
        if not ReglaCalculo.objects.filter(codigo=codigo).exists():
            ReglaCalculo.objects.create(codigo=codigo, nombre=nombre, descripcion=descripcion, parametros=parametros,
                                        version=1, vigente_desde=date(2026, 1, 1))


def quitar(apps, schema_editor):
    apps.get_model("nomina", "ReglaCalculo").objects.filter(codigo__in=[r[0] for r in REGLAS], periodos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nomina", "0002_triggers_nomina"),
        ("personal", "0003_observaciones"),
    ]

    operations = [migrations.RunPython(crear, quitar)]
