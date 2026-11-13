"""
Acta de llamado de atención por atraso (reglas de penalización v2).

Un atraso no justificado que pasa el último tramo de multa (desde las 9:30) es llamado de
atención: el empleado no ingresa a la oficina, se cobra la multa y el día cuenta como falta.
El administrador genera el acta en PDF para que la firme el empleado; una copia queda en su
expediente (Documentos, tipo «Acta de llamado de atención»).
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.http import Http404
from django.utils import timezone

from . import calculo


def es_llamado(novedad, reglas: dict) -> bool:
    from apps.asistencia.models import NovedadAsistencia

    return (novedad.tipo == NovedadAsistencia.Tipo.ATRASO and novedad.estado != NovedadAsistencia.Estado.JUSTIFICADA
            and calculo.multa_por_atraso(novedad.minutos or 0, reglas) is None)


def numero_de_llamado(novedad, reglas: dict) -> int:
    """Cuántos llamados de atención lleva en el último año contando este (atrasos largos + Observaciones)."""
    from apps.asistencia.models import NovedadAsistencia
    from apps.personal.models import ObservacionEmpleado

    desde = novedad.fecha - timedelta(days=int(reglas.get("ventana_llamados_dias", 365)))
    ultimo_tramo = max((t["hasta_minutos"] for t in reglas.get("atraso_multas", [])), default=10)
    por_atraso = NovedadAsistencia.objects.filter(
        empleado_id=novedad.empleado_id, tipo=NovedadAsistencia.Tipo.ATRASO, fecha__range=(desde, novedad.fecha),
        minutos__gt=ultimo_tramo,
    ).exclude(estado=NovedadAsistencia.Estado.JUSTIFICADA).count()
    manuales = ObservacionEmpleado.objects.filter(
        empleado_id=novedad.empleado_id, tipo=ObservacionEmpleado.Tipo.LLAMADO_ATENCION,
        creado_en__date__range=(desde, novedad.fecha),
    ).count()
    return por_atraso + manuales


def acta_pdf(novedad) -> bytes:
    from apps.documentos.contratos import _latin1, dinero

    reglas = calculo.parametros("PENALIZACIONES", novedad.fecha)
    if not es_llamado(novedad, reglas):
        raise Http404("Ese atraso no es un llamado de atención.")
    e = calculo.estimar_mes(novedad.empleado, novedad.fecha.year, novedad.fecha.month)
    emp = novedad.empleado
    pdf, ancho = calculo._documento_pdf("ACTA DE LLAMADO DE ATENCIÓN", "Llamado de atencion", e)
    # Misma fecha interna siempre: el mismo atraso genera el mismo archivo y no se duplica en el expediente.
    pdf.set_creation_date(datetime.combine(novedad.fecha, time(12), tzinfo=timezone.get_current_timezone()))

    hora_entrada = reglas.get("hora_entrada", "09:00")
    llegada = timezone.localtime(novedad.registro.entrada).strftime("%H:%M") if novedad.registro_id else None
    dia = f"{calculo.DIAS_SEMANA[novedad.fecha.weekday()]} {novedad.fecha:%d/%m/%Y}"
    llamado_desde = max((t["hasta_minutos"] for t in reglas.get("atraso_multas", [])), default=10) + 1

    calculo._pdf_seccion(pdf, ancho, "Hecho")
    pdf.ln(2)
    pdf.set_text_color(30)
    pdf.set_font("Helvetica", size=10.5)
    texto = (f"Se deja constancia de que {emp.nombres} {emp.apellidos}, con cédula {emp.identificacion}, el día {dia} "
             + (f"registró su ingreso a las {llegada}, " if llegada else "")
             + f"con {novedad.minutos} minutos de atraso respecto de la hora de entrada ({hora_entrada}), sin justificación. "
             f"Conforme al reglamento interno, con un atraso de {llamado_desde} minutos o más el empleado no ingresa a la "
             f"oficina sin justificación y se registra este llamado de atención.")
    pdf.multi_cell(ancho, 5.6, _latin1(texto), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    calculo._pdf_seccion(pdf, ancho, "Consecuencias")
    multa = reglas.get("atraso_llamado_multa")
    if multa:
        calculo._pdf_fila(pdf, ancho, "Multa por atraso (llamado de atención)", dinero(Decimal(str(multa))))
    if reglas.get("atraso_llamado_es_falta"):
        pct = Decimal(str(reglas.get("falta_no_justificada_porcentaje", "10")))
        calculo._pdf_fila(pdf, ancho, f"El día cuenta como falta no justificada ({calculo._porcentaje(pct)} % del sueldo)", "")
    if reglas.get("tope_penalizaciones_porcentaje") is not None:
        calculo._pdf_fila(pdf, ancho, f"Las multas y faltas del mes no superan el "
                                      f"{calculo._porcentaje(reglas['tope_penalizaciones_porcentaje'])} % del sueldo", "")
    numero, para_visto_bueno = numero_de_llamado(novedad, reglas), int(reglas.get("llamados_para_visto_bueno", 3))
    calculo._pdf_fila(pdf, ancho, f"Llamados de atención en el último año (con este)", f"{numero} de {para_visto_bueno}", negrita=True)
    if numero >= para_visto_bueno:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(160, 30, 30)
        pdf.multi_cell(ancho, 5.2, _latin1(f"Con {numero} llamados de atención en el último año procede el visto bueno."),
                       new_x="LMARGIN", new_y="NEXT")

    pdf.ln(5)
    calculo._pdf_seccion(pdf, ancho, "Descargo del empleado")
    pdf.set_draw_color(*calculo._LINEA)
    for _ in range(3):
        pdf.ln(8)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + ancho, pdf.get_y())

    calculo._pdf_firmas_y_pie(pdf, ancho, e, f"Atraso del {novedad.fecha:%d/%m/%Y} ({novedad.minutos} min).",
                              firma_derecha="Firma Administración")
    return bytes(pdf.output())


def guardar_en_expediente(novedad, pdf: bytes, usuario):
    """Copia del acta en el expediente del empleado (si ya está la misma, no la duplica)."""
    from apps.documentos.servicios import guardar_documento

    nombre = f"Llamado de atencion {novedad.fecha:%Y-%m-%d} {novedad.empleado.apellidos} {novedad.empleado.nombres}.pdf"
    return guardar_documento(ContentFile(pdf, name=nombre), tipo_codigo="LLAMADO_ATENCION", empleado=novedad.empleado,
                             usuario=usuario, descripcion=f"Atraso de {novedad.minutos} min del {novedad.fecha:%d/%m/%Y}")
