"""
Plantilla de contrato DE EJEMPLO, para probar la generación mientras la empresa entrega su texto oficial.
No es un modelo legal: debe reemplazarse por el contrato aprobado por la empresa y su asesor legal.
"""
from django.db import migrations

NOMBRE = "Contrato individual de trabajo (EJEMPLO)"

CONTENIDO = """# CONTRATO INDIVIDUAL DE TRABAJO

**MODELO DE EJEMPLO - NO USAR PARA FIRMAR.** Reemplace este texto por el contrato aprobado por la empresa. Los datos entre corchetes [ ] deben completarse en la plantilla; los que están entre llaves se llenan solos con la ficha del empleado.

En la ciudad de Quito, a {{fecha_actual_texto}}, comparecen: por una parte [NOMBRE DE LA EMPRESA], con RUC [RUC DE LA EMPRESA], representada por [NOMBRE DEL REPRESENTANTE LEGAL], a quien en adelante se denominará **EL EMPLEADOR**; y por otra parte **{{nombre_completo}}**, con {{tipo_identificacion}} No. {{identificacion}}, nacido(a) el {{fecha_nacimiento}}, con domicilio en {{direccion}}, teléfono {{telefono}} y correo {{email}}, a quien en adelante se denominará **EL TRABAJADOR**. Ambas partes acuerdan celebrar el presente contrato al tenor de las siguientes cláusulas:

**PRIMERA - OBJETO.** EL TRABAJADOR se compromete a prestar sus servicios lícitos y personales en el cargo de **{{cargo}}**, cumpliendo las funciones propias del puesto y las instrucciones de EL EMPLEADOR.

**SEGUNDA - PLAZO.** El presente contrato es de tipo {{tipo_contrato}} y rige desde el {{fecha_inicio_texto}}. [Si el contrato tiene plazo, indicar la fecha de terminación: {{fecha_fin}}.]

**TERCERA - REMUNERACIÓN.** EL EMPLEADOR pagará a EL TRABAJADOR una remuneración mensual de {{salario}} ({{salario_en_letras}}), más los beneficios de ley.

**CUARTA - JORNADA.** [Describir la jornada y el horario de trabajo.]

**QUINTA - OBLIGACIONES.** [Detallar las obligaciones de las partes, confidencialidad y demás cláusulas acordadas.]

Para constancia, las partes firman en dos ejemplares de igual valor.



______________________________
EL EMPLEADOR
[NOMBRE DEL REPRESENTANTE LEGAL]



______________________________
EL TRABAJADOR
{{nombre_completo}}
{{tipo_identificacion}} No. {{identificacion}}
"""


def crear(apps, schema_editor):
    PlantillaContrato = apps.get_model("documentos", "PlantillaContrato")
    if PlantillaContrato.objects.filter(nombre=NOMBRE).exists():
        return
    import re
    PlantillaContrato.objects.create(
        nombre=NOMBRE, version=1, tipo_contrato="", contenido=CONTENIDO, activa=True,
        campos_variables=sorted(set(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", CONTENIDO))),
    )


def quitar(apps, schema_editor):
    PlantillaContrato = apps.get_model("documentos", "PlantillaContrato")
    PlantillaContrato.objects.filter(nombre=NOMBRE, contratos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("documentos", "0002_tipo_titulo_bachiller")]

    operations = [migrations.RunPython(crear, quitar)]
