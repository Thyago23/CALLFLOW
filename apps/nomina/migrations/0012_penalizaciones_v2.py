"""
Penalizaciones v2 (desde noviembre de 2026). Entrada a las 9:00; cada minuto empezado cuenta.

  9:00:01 – 9:09  (1 a 9 min)   → multa $1
  9:09:01 – 9:29  (10 a 29 min) → multa $3
  9:29:01 en adelante (30+ min) → no ingresa a la oficina: llamado de atención, multa $4
                                  (las dos anteriores sumadas) y el día cuenta como falta no justificada.
  Tope: las multas por atraso más las faltas no justificadas del mes no superan el 10 % del sueldo.

La versión 1 queda vigente hasta el 31 de octubre de 2026, para que los meses anteriores se
sigan calculando igual. También crea el tipo de documento del acta de llamado de atención.
"""
from datetime import date

from django.db import migrations

DESDE = date(2026, 11, 1)
HASTA_V1 = date(2026, 10, 31)

PARAMETROS_V2 = {
    "hora_entrada": "09:00",
    "falta_no_justificada_porcentaje": "10",
    "falta_justificada_monto": "13.33",
    "atraso_multas": [{"hasta_minutos": 9, "multa": "1.00"}, {"hasta_minutos": 29, "multa": "3.00"}],
    "atraso_llamado_multa": "4.00",
    "atraso_llamado_es_falta": True,
    "tope_penalizaciones_porcentaje": "10",
    "llamados_para_visto_bueno": 3,
    "ventana_llamados_dias": 365,
    "aviso_multas_porcentaje": "10",
}


def crear(apps, schema_editor):
    ReglaCalculo = apps.get_model("nomina", "ReglaCalculo")
    TipoDocumento = apps.get_model("documentos", "TipoDocumento")
    TipoDocumento.objects.update_or_create(
        codigo="LLAMADO_ATENCION", defaults={"nombre": "Acta de llamado de atención", "requerido_ingreso": False, "activo": True}
    )
    if ReglaCalculo.objects.filter(codigo="PENALIZACIONES", version=2).exists():
        return
    v1 = ReglaCalculo.objects.filter(codigo="PENALIZACIONES", version=1).first()
    if v1 and (v1.vigente_hasta is None or v1.vigente_hasta >= DESDE):
        v1.vigente_hasta = HASTA_V1
        v1.save(update_fields=["vigente_hasta"])
    ReglaCalculo.objects.create(
        codigo="PENALIZACIONES", version=2, vigente_desde=DESDE,
        nombre="Penalizaciones por faltas y atrasos",
        descripcion="1–9 min $1 · 10–29 min $3 · 30+ min: llamado de atención, $4 y falta. Tope: 10 % del sueldo.",
        parametros=PARAMETROS_V2,
    )


def quitar(apps, schema_editor):
    ReglaCalculo = apps.get_model("nomina", "ReglaCalculo")
    ReglaCalculo.objects.filter(codigo="PENALIZACIONES", version=2, periodos__isnull=True).delete()
    ReglaCalculo.objects.filter(codigo="PENALIZACIONES", version=1, vigente_hasta=HASTA_V1).update(vigente_hasta=None)
    apps.get_model("documentos", "TipoDocumento").objects.filter(codigo="LLAMADO_ATENCION", documentos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nomina", "0011_renombrar_equipos"),
        ("documentos", "0008_contrato_automatico"),
    ]

    operations = [migrations.RunPython(crear, quitar)]
