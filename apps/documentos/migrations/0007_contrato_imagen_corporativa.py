"""
Plantilla «Contrato de servicios profesionales de imagen corporativa y visual» (Word de la empresa) y el cargo
«Imagen corporativa», para que se proponga sola al generar el contrato de esa persona.

El texto es el del Word «CONTRATO DE SERVICIOS PROFESIONALES DE IMAGEN CORPORATIVA Y VISUAL.docx». Lo que en el Word
estaba resaltado para llenar a mano ahora sale de la ficha (llenada con el CV): nombre y cédula; y de lo que se indica
al generar el contrato: fecha de inicio (fecha de suscripción), fecha de fin (plazo en meses) y retribución mensual.
"""
from django.db import migrations

NOMBRE = "Contrato de servicios profesionales de imagen corporativa y visual"
CARGO = "Imagen corporativa"

CONTENIDO = """# CONTRATO DE SERVICIOS PROFESIONALES DE IMAGEN CORPORATIVA Y VISUAL

En la ciudad de Quito, provincia de Pichincha, a los {{fecha_inicio_dia}} días del mes de {{fecha_inicio_mes}} del año {{fecha_inicio_anio_letras}}, comparecen a la celebración del presente Contrato de PRESTACIÓN DE SERVICIOS, que celebran:

A. Por una parte, la empresa MULTIVISA S.A.S, con RUC 1793207878001, con domicilio en la Provincia de Pichincha, en la ciudad de Quito, calles Zurriago y El Vengador, a quien se denominará de aquí en adelante como EL CONTRATANTE.

B. Por otra parte, la señorita {{nombre_completo_mayusculas}} portadora de cédula de ciudadanía, identidad, pasaporte Nº {{identificacion_con_guion}}, con domicilio en la provincia de Pichincha, en la ciudad de Quito, a quien se denominará de aquí en adelante como LA PROFESIONAL.

De manera voluntaria y de común acuerdo suscriben el presente contrato, a quienes en lo sucesivo se los denominará como LAS PARTES, en los términos y condiciones siguientes:

## CLÁUSULA PRIMERA. - ANTECEDENTES

1.1 EL CONTRATANTE es una persona jurídica, quien se encuentra registrado en el Servicio de Rentas Internas de manera activa y desempeña su función económica principal como actividades de servicios de turismo, otros.

1.2 LA PROFESIONAL es una persona natural habitualmente dedicada a la actividad de comisionista, como profesional en el ámbito de imagen corporativa y/o imagen pública, quien está en disposición de distribuir, promocionar productos de índole publicitario MULTIVISA S.A.S, con los productos y proyectos que la misma tiene en vigencia.

## CLÁUSULA SEGUNDA. - OBJETO DEL CONTRATO

2.1 Por el presente contrato, LA PROFESIONAL se obliga a realizar 8 videos publicitarios cada mes en favor de EL CONTRATANTE la promoción audiovisual, videos para historias y/o estados, imagen en medios (FACEBOOK, YOUTUBE INSTAGRAM, RADIO FM Y, RADIO DIGITAL, TV) como también la participación de los videos vivenciales dentro y fuera de la ciudad, o país según su disponibilidad junto a MULTIVISAS.

Así mismo, LA PROFESIONAL establece su compromiso en realizar las campañas publicitarias como colaboración entre sus Redes Sociales y las Redes Sociales de EL CONTRATANTE.

2.2 En contraprestación, EL CONTRATANTE se obliga a pagar a LA PROFESIONAL, el monto de la retribución prevista en la cláusula quinta, con base a un establecido de costos de publicidad.

## CLÁUSULA TERCERA. - CARACTERES DEL CONTRATO

3.1 Al celebrar el presente contrato, se manifiesta que LA PROFESIONAL está clara que su esquema publicitario debe ejecutarse en base a lo establecido en las cláusulas expresas en el mismo, por lo tanto, se expresa que tiene el conocimiento para realizar una promoción audiovisual que permita promocionar la marca y proyectos publicitarios de EL CONTRATANTE.

3.2 La relación derivada del presente contrato tiene carácter comercial, no existiendo, en consecuencia, subordinación alguna o dependencia de LA PROFESIONAL respecto de EL CONTRATANTE, teniendo entera libertad para escoger la manera como desempeñar el encargo, siempre que su labor se enmarque dentro de las condiciones previstas en este contrato.

3.4 LA PROFESIONAL está clara que, en caso de no realizar la producción de los materiales publicitarios para los que fue contratada, EL CONTRATANTE podrá realizar los descuentos del caso sobre cada incumplimiento que se genere, como también, el incumplimiento del este CONTRATO podrá dar atribución a EL CONTRATANTE a suspender el CONTRATO con las sanciones que deriven del mismo.

## CLÁUSULA CUARTA. - PLAZO DE CONTRATO

4.1 El presente contrato tendrá una duración de {{plazo_meses}} meses según lo establecido en este contrato y, contado a partir de la fecha de suscripción del presente documento siendo oportuno indicar que pasado este tiempo el contrato podrá ser renovado según un acuerdo de LAS PARTES.

4.2 La vigencia del presente contrato puede terminar con anterioridad a la fecha prevista, en caso que LA PROFESIONAL no cumpla íntegramente con las funciones asignadas en su cargo antes del vencimiento del plazo señalado o de mutuo acuerdo, como también lo podrán ampliar en caso que LAS PARTES así lo estipulen.

4.3 Se establece que el material producido con LA PROFESIONAL Y EL CONTRATANTE en base a, videos promocionales, vivenciales, entre otros y que se realizan en beneficio de la empresa MULTIVISA S.A.S,, serán de uso constante y de propiedad exclusiva de la empresa MULTIVISA S.A.S,.

## CLÁUSULA QUINTA. - RETRIBUCIÓN, FORMA Y OPORTUNIDAD DE PAGO

5.1 EL CONTRATANTE se compromete a pagar a LA PROFESIONAL por sus servicios de publicidad e imagen una retribución de {{salario_corto}} {{salario_letras_texto}}, esto acorde al servicio de publicidad y promoción de la marca MULTIVISA S.A.S,.

5.2 LA PROFESIONAL recibirá esta retribución por parte de EL CONTRATANTE hasta los primeros 8 días de cada mes por concepto de pago una vez completados los 08 videos publicitarios con el proceso de trabajo en compartido en Redes sociales cada mes que EL CONTRATANTE solicita a través de este contrato y, para ello LA PROFESIONAL deberá entregar la respectiva factura para mantener la conformidad ante la actividad de la empresa MULTIVISA S.A.S, mantiene según su actividad jurídica y comercial.

## CLÁUSULA SEXTA. - OBLIGACIONES Y DERECHOS DE LAS PARTES.

6.1 Queda expresamente establecido que LA PROFESIONAL va a realizar la promoción que EL CONTRATANTE indica en la cláusula 2 en los tiempos mencionados para la producción de publicidad e imagen para la empresa MULTIVISA S.A.S,.

6.2 A su vez, EL CONTRATANTE asumirá, respecto de LA PROFESIONAL, las obligaciones que se hubiese contraído con terceros en la ejecución del presente contrato.

6.3 LA PROFESIONAL deberá comunicar oportunamente a EL CONTRATANTE la dificultad o imposibilidad de la ejecución del encargo, señalando en todos los casos, las causas que motivan tales complicaciones para que se pueda realizar una reorganización en los esquemas de trabajo y así tener el cumplimiento de las obligaciones del presente CONTRATO.

## CLÁUSULA SÉPTIMA. - GASTOS Y TRIBUTOS

7.1 Todos los gastos administrativos que se generen por la ejecución de este contrato serán asumidos por EL CONTRATANTE, siendo el caso que, si LA PROFESIONAL solicitará beneficios extras o gastos relativos no establecidos, LA PROFESIONAL asumirá los mismos.

## CLÁUSULA OCTAVA. - CLÁUSULA ARBITRAL

8.1 Las controversias que pudieran suscitarse en torno al presente contrato, serán sometidas a arbitraje, mediante un Tribunal Arbitral integrado por expertos en la materia, uno de ellos designado de común acuerdo por las partes, quien lo presidirá, y los otros designados por cada uno de ellos.

## CLÁUSULA NOVENA. - DOMICILIO

9.1 Para la validez de todas las comunicaciones y notificaciones a LAS PARTES con motivo de la ejecución de este contrato, ambas señalan como sus respectivos domicilios los indicados en la introducción de este documento.

El cambio de domicilio de cualquiera de las partes surtirá efecto desde la fecha de comunicación de dicho cambio a la otra parte, por vía notarial.

## CLÁUSULA DÉCIMA PRIMERA. - APLICACIÓN SUPLETORIA DE LA LEY

10.1 En todo lo no previsto por LAS PARTES en el presente contrato, ambas se someten a lo establecido por las normas relativas al contrato y demás normas legales que resulten aplicables.

## CLÁUSULA DÉCIMA SEGUNDA. - CONFORMIDAD DE ACUERDO

12.1 En señal de conformidad, LAS PARTES suscriben este documento en la ciudad de Quito, a los {{fecha_inicio_dia}} días del mes de {{fecha_inicio_mes}} del año {{fecha_inicio_anio}}.

[[espacio]]

MULTIVISA S.A.S,
1793207878001
EL CONTRATANTE

[[espacio]]

**{{nombre_completo_mayusculas}}**
LA PROFESIONAL
Nº {{identificacion_con_guion}}
"""

VARIABLES = ["fecha_inicio_anio", "fecha_inicio_anio_letras", "fecha_inicio_dia", "fecha_inicio_mes", "identificacion_con_guion",
             "nombre_completo_mayusculas", "plazo_meses", "salario_corto", "salario_letras_texto"]


def crear(apps, schema_editor):
    Plantilla = apps.get_model("documentos", "PlantillaContrato")
    Cargo = apps.get_model("personal", "Cargo")
    cargo, _ = Cargo.objects.get_or_create(nombre=CARGO)
    if Plantilla.objects.filter(nombre=NOMBRE).exists():
        return
    Plantilla.objects.create(nombre=NOMBRE, version=1, tipo_contrato="", contenido=CONTENIDO, campos_variables=VARIABLES,
                             activa=True, estilo="OFICIO", cargo=cargo)


def quitar(apps, schema_editor):
    apps.get_model("documentos", "PlantillaContrato").objects.filter(nombre=NOMBRE, contratos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documentos", "0006_estilo_oficio"),
    ]

    operations = [migrations.RunPython(crear, quitar)]
