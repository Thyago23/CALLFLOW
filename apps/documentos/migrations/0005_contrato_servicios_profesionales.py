"""
Plantilla «Contrato de servicios profesionales bajo comisiones» (Word de la empresa), para los asesores de ventas.

El texto es el del Word «CONTRATO DE SERVICIOS PROFESIONALES EDITADO.docx». Los datos del asesor salen de su ficha
(llenada con su CV): apellidos y nombres, tipo y número de identificación; y de lo que se indica al generar: fecha de
inicio (fecha de suscripción) y retribución mensual. Se propone sola para el cargo «Asesor de ventas».
"""
from django.db import migrations

NOMBRE = "Contrato de servicios profesionales bajo comisiones"

CONTENIDO = """# CONTRATO DE SERVICIOS PROFESIONALES
# BAJO COMISIONES

En la ciudad de Quito, provincia de Pichincha, comparecen a la celebración del presente Contrato de Servicios por Comisiones, que celebran:

A. Por una parte, el señor **RAÚL IVÁN TAPIA DÍAZ**, persona natural con RUC 1719049759001, con domicilio en la provincia de Pichincha, en la ciudad de Quito; a quien se denominará de aquí en adelante como **EL COMITENTE**.

B. Por otra parte, el señor, señora, señorita, **{{nombre_completo_mayusculas}}** con cédula de identidad, ciudadanía, pasaporte Nº **{{identificacion}}** con domicilio en la provincia de Pichincha, en la ciudad de Quito, a quien se denominará de aquí en adelante como **EL PROFESIONAL**.

De manera voluntaria y de común acuerdo suscriben el presente contrato, a quienes en lo sucesivo se los denominará como **LAS PARTES,** en los términos y condiciones siguientes:

## CLÁUSULA PRIMERA. - ANTECEDENTES

1.1 **EL COMITENTE** es una persona natural, quien se encuentra registrado en el Servicio de Rentas Internas de manera activa y desempeña su función económica principal como actividades de las agencias de viajes dedicadas principalmente a comercializar servicios de viajes, de viajes organizados, de transporte y de alojamiento, al por mayor o al por menor, al público en general y a clientes comerciales.

1.2 **EL PROFESIONAL** es una persona natural, quien se encuentra registrada en el Servicio de Rentas Internas de manera activa para facturar por sus servicios como comisionista.

## CLÁUSULA SEGUNDA. - OBJETO DEL CONTRATO

2.1 **EL COMITENTE** ha decidido contratar los servicios profesionales como comisionista a **EL PROFESIONAL** para la comercialización de los servicios que **EL COMITENTE** mantiene.

2.2 Por el presente contrato, **EL PROFESIONAL** se obliga a realizar en favor de **EL COMITENTE** la comercialización de BENEFICIOS TURÍSTICOS Y ASESORÍA DE VISA en los términos estipulados en este contrato. En contraprestación**, EL COMITENTE** se obliga a pagar a **EL PROFESIONAL,** el monto de la retribución en base a la cláusula quinta.

## CLÁUSULA TERCERA. - CARACTERES DEL CONTRATO

3.1 Al celebrar el presente contrato, se manifiesta que **EL PROFESIONAL** está claro que su trabajo debe ejecutarse en base a lo establecido en las cláusulas expresas en el mismo, por lo tanto, se manifiesta que tiene el conocimiento para realizar un trabajo óptimo liberando de responsabilidades al **COMITENTE** ante una mala ejecución de labores de **EL PROFESIONAL.**

3.2 Queda expresamente establecido que las personas con quien celebrase contratos EL PROFESIONAL, no tendrán acción contra EL COMITENTE, ni éste contra aquéllas quedando a salvo siempre las que respectivamente correspondan a **EL COMITENTE** y a **EL PROFESIONAL** entre sí.

3.3 La relación derivada del presente contrato tiene carácter comercial, no existiendo, en consecuencia, subordinación alguna o relación de dependencia de **EL PROFESIONAL** respecto de **EL COMITENTE**, teniendo entera libertad para escoger la manera como desempeñar el encargo, siempre que su labor se enmarque dentro de las condiciones previstas en este CONTRATO DE PRESTACIÓN DE SERVICIOS PROFESIONALES BAJO COMISIÓN.

## CLÁUSULA CUARTA. - PLAZO DE CONTRATO

4.1 El presente contrato tendrá una duración de 3 meses (90 días según lo establecido y autorizado por la autoridad competente), contados a partir de la fecha de suscripción del presente documento los cuales serían a prueba.

4.2 Durante la vigencia del presente contrato, **EL PROFESIONAL** se obliga a laborar exclusivamente para **EL COMITENTE**, sin que pueda realizar otras funciones ni por cuenta propia ni por cuenta de terceros, debiendo además guardar absoluta reserva y discreción sobre las informaciones y datos de **EL COMITENTE** que llegaren a su conocimiento en razón del trabajo que realiza. Expresamente convienen las partes en que las mejoras en los procedimientos, los descubrimientos e inversiones, así como los trabajos y resultados que se obtengan por las actividades de **EL PROFESIONAL** mientras este preste sus servicios a **EL COMITENTE**, serán de propiedad exclusiva de **EL COMITENTE**, el cual incluso podrá patentar o registrar a su nombre tales inventos o mejoras.

4.3 **EL PROFESIONAL** manifiesta su conformidad en asistir a las instalaciones que **EL COMITENTE** pone a consideración para la buena ejecución de las tareas asignadas a EL PROFESIONAL, así como también mantendrá las herramientas necesarias para el cumplimiento de sus metas asignadas.

4.4 La vigencia del presente contrato puede terminar con anterioridad a la fecha prevista, en caso que **EL PROFESIONAL** no cumpla íntegramente con las funciones asignadas en su cargo antes del vencimiento del plazo señalado o de mutuo acuerdo, notificando el proceso con la anticipación del caso.

## CLÁUSULA QUINTA. - RETRIBUCIÓN, FORMA Y OPORTUNIDAD DE PAGO

5.1 **EL COMITENTE** se compromete a pagar a **EL PROFESIONAL** por sus servicios profesionales una retribución económica de {{salario_corto}}, esto por su meta de venta de $1,300 y, las comisiones por venta una vez pasado la primera meta antes señalada en esta cláusula serán de la siguiente manera:

| Diferencia de ventas (pasada la meta) | Porcentaje de comisión |
| $1     -     $1300 | Pago Base {{salario_corto}} |
| $1301     -     $3000 | 11% |
| $3001     -     -------- | 15% |

5.2 Si **EL PROFESIONAL** no llegara a alcanzar la meta establecida, solo percibirá su retribución o proporcional a los servicios prestados y generados, esto en conformidad con **EL COMITENTE**

5.3 **EL PROFESIONAL** para poder realizar el cobro de sus servicios, deberá presentar **una factura mensual de su propio RUC a nombre de EL COMITENTE**, la cual deberá ser emitida con fecha actual y entregada los primeros cinco días de cada mes posterior al trabajado.

5.4 El pago se lo realizará los primeros días de cada mes, teniendo como fecha máxima de pago el día 08 de cada mes. Este pago de preferencia se lo realizará en efectivo o transferencia bancaria acordado entre las partes sin afectación de la ley de bancarización vigente en Ecuador.

5.5 Aclaratoria, se determina entre **LAS PARTES** que las retribuciones económicas estarán estrictamente ligadas al cumplimiento y comercialización de los servicios que **EL COMITENTE** tiene para clientes, empresas entre otros, de ser el caso y, que **EL PROFESIONAL** está claro que, si no ha generado comercialización o cierre de venta de clientes, **EL COMITENTE** no está obligado a establecer retribuciones económicas.

## CLÁUSULA SEXTA. - OBLIGACIONES Y DERECHOS DE LAS PARTES

6.1 Queda expresamente establecido el precio de cada producto a comercializar y la forma de transaccionar de los mismos. Toda esta información es transmitida por parte de **EL COMITENTE** en el momento de capacitación y adaptación con los diferentes productos. (Capacitaciones no remuneradas)

6.2 A su vez, **EL COMITENTE** asumirá respecto de **EL PROFESIONAL** todas las obligaciones que éste hubiese contraído con terceros en la ejecución del presente contrato.

6.3 **EL PROFESIONAL** deberá realizar todos los actos dirigidos al cumplimiento y ejecución del presente contrato en forma personal y directa, no pudiendo valerse de terceros que obren en delegaciones o sustitución en la realización del encargo recibido.

6.4 **EL PROFESIONAL** deberá comunicar oportunamente a **EL COMITENTE** la dificultad o imposibilidad de la ejecución del encargo, señalado en todos los casos, las causas que motivan tales complicaciones.

6.5 **EL PROFESIONAL** deberá comunicar por escrito a **EL COMITENTE** de manera mensual sobre el desempeño de sus actividades mediante una hoja de producción.

## CLÁUSULA SÉPTIMA. - CLÁUSULA PENAL

7.1 En caso de existir incumplimiento, mala fe o dolo por parte de **EL PROFESIONAL** en la entrega del producto final a terceros, el mismo será notificado para que se someta a la sanción pertinente por parte de las autoridades mediante lo establecido en el Código Civil.

7.2 **El COMISIONISTA** deberá ejecutar su trabajo con los términos y condiciones que **EL COMITENTE** refleja con sus políticas internas de trabajo y con las cláusulas detalladas en este contrato.

## CLÁUSULA OCTAVA. - CLÁUSULA RESOLUTORIA EXPRESA

8.1 El incumplimiento de las obligaciones asumidas por **EL COMISIONISTA** en las cláusulas detalladas anteriormente, constituirá causal de disolución del presente contrato. En consecuencia, la disolución se producirá de pleno derecho cuando **EL COMITENTE** comunique, por escrito, a **EL COMISIONISTA** que quiere valerse de esta cláusula.

## CLÁUSULA NOVENA. - GASTOS Y TRIBUTOS

9.1 Todos los gastos administrativos que se generen por la ejecución de este contrato serán asumidos por **EL COMITENTE**.

## CLÁUSULA DÉCIMA. - CLÁUSULA ARBITRAL

10.1 Las controversias que pudieran suscitarse en torno al presente contrato, serán sometidas a arbitraje, mediante un Tribunal Arbitral integrado por expertos en la materia, uno de ellos designado de común acuerdo por las partes, quien lo presidirá, y los otros designados por cada uno de ellos.

## CLÁUSULA DÉCIMO PRIMERA. - DOMICILIO

11.1 Para la validez de todas las comunicaciones y notificaciones a las partes con motivo de la ejecución de este contrato, ambas señalan como sus respectivos domicilios los indicados en la introducción de este documento. El cambio de domicilio de cualquiera de las partes surtirá efecto desde la fecha de comunicación de dicho cambio a la otra parte.

## CLÁUSULA DÉCIMO SEGUNDA. - APLICACIÓN SUPLETORIA DE LA LEY

12.1 En todo lo no previsto por las partes en el presente contrato, ambas se someten a lo establecido por las normas relativas al contrato y demás normas legales que resulten aplicables.

## CLÁUSULA DÉCIMO TERCERA. - CONFORMIDAD DE ACUERDOS

13.1 En señal de conformidad, las partes suscriben este documento para respaldo mutuo en la capital del Ecuador.
Quito D.M., {{fecha_inicio_legal}}.

[[espacio]]

**RAÚL IVÁN TAPIA DÍAZ**
**RUC 1719049759001**
**EL COMITENTE.**

[[espacio]]

**{{nombre_completo_mayusculas}}**
**{{tipo_identificacion_mayusculas}} Nº {{identificacion}}**
**EL PROFESIONAL**
"""

VARIABLES = ["fecha_inicio_legal", "identificacion", "nombre_completo_mayusculas", "salario_corto", "tipo_identificacion_mayusculas"]


def crear(apps, schema_editor):
    Plantilla = apps.get_model("documentos", "PlantillaContrato")
    Cargo = apps.get_model("personal", "Cargo")
    if Plantilla.objects.filter(nombre=NOMBRE).exists():
        return
    cargo = Cargo.objects.filter(nombre__iexact="Asesor de ventas").first() or Cargo.objects.create(nombre="Asesor de ventas")
    Plantilla.objects.create(nombre=NOMBRE, version=1, tipo_contrato="", contenido=CONTENIDO, campos_variables=VARIABLES,
                             activa=True, estilo="FORMAL", cargo=cargo)


def quitar(apps, schema_editor):
    apps.get_model("documentos", "PlantillaContrato").objects.filter(nombre=NOMBRE, contratos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documentos", "0004_plantilla_estilo_cargo"),
        ("core", "0013_renombrar_roles"),  # el cargo ya se llama «Asesor de ventas»
    ]

    operations = [migrations.RunPython(crear, quitar)]
