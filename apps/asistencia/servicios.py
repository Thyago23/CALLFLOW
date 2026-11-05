"""
Reglas de asistencia (SCRUM-18 a SCRUM-21).

- Entrada/salida: una marcación por empleado y día.
- Atraso: entrada después de la hora del horario + tolerancia. Se registran los minutos
  desde la hora de entrada (ej.: horario 08:00, tolerancia 5, llega 08:12 -> 12 min).
- Salida anticipada: salida antes de la hora de salida del horario (con la misma tolerancia).
- Ausencia: día laborable sin entrada.
- Las novedades nacen PENDIENTES; el administrador las clasifica como justificadas o no.
  Si se corrige una marcación, solo se recalculan las novedades que siguen pendientes.
"""
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.personal.models import Empleado

from .models import AsignacionHorario, Horario, NovedadAsistencia, RegistroAsistencia

Tipo = NovedadAsistencia.Tipo
Estado = NovedadAsistencia.Estado


# --------------------------------------------------------------------------- horarios
def asignaciones_vigentes(fecha: date):
    return AsignacionHorario.objects.filter(
        Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=fecha), vigente_desde__lte=fecha
    ).select_related("horario")


def horario_de(empleado, fecha: date) -> Horario | None:
    asignacion = asignaciones_vigentes(fecha).filter(empleado=empleado).first()
    return asignacion.horario if asignacion else None


def es_laborable(horario: Horario | None, fecha: date) -> bool:
    return horario is not None and fecha.isoweekday() in horario.dias_laborables


def _minutos_entre(desde: time, hasta: time) -> int:
    base = date(2000, 1, 1)
    return int((datetime.combine(base, hasta) - datetime.combine(base, desde)).total_seconds() // 60)


def minutos_atraso(horario: Horario | None, fecha: date, entrada: datetime) -> int:
    """
    Minutos de atraso, contando cada minuto empezado: 9:00:01 → 1, 9:01:00 → 1, 9:01:01 → 2.
    Los segundos importan: la multa más baja (de 9:00 a 9:01) depende de ellos.
    """
    if not es_laborable(horario, fecha):
        return 0
    llegada = timezone.localtime(entrada)
    segundos = (llegada - datetime.combine(llegada.date(), horario.hora_entrada, tzinfo=llegada.tzinfo)).total_seconds()
    minutos = math.ceil(segundos / 60) if segundos > 0 else 0
    return minutos if minutos > horario.minutos_tolerancia else 0


def minutos_salida_anticipada(horario: Horario | None, fecha: date, salida: datetime | None) -> int:
    if salida is None or not es_laborable(horario, fecha):
        return 0
    minutos = _minutos_entre(timezone.localtime(salida).time().replace(second=0, microsecond=0), horario.hora_salida)
    return minutos if minutos > horario.minutos_tolerancia else 0


# --------------------------------------------------------------------------- novedades automáticas
def _sincronizar_novedad(empleado, fecha, tipo, minutos, registro):
    """Crea, actualiza o elimina una novedad automática, sin tocar las ya clasificadas."""
    actual = NovedadAsistencia.objects.filter(empleado=empleado, fecha=fecha, tipo=tipo).first()
    if minutos > 0:
        if actual is None:
            NovedadAsistencia.objects.create(empleado=empleado, fecha=fecha, tipo=tipo, minutos=minutos, registro=registro)
        elif actual.estado == Estado.PENDIENTE:
            actual.minutos, actual.registro = minutos, registro
            actual.save(update_fields=["minutos", "registro", "actualizado_en"])
    elif actual is not None and actual.estado == Estado.PENDIENTE:
        actual.delete()


def evaluar_registro(registro: RegistroAsistencia) -> None:
    horario = horario_de(registro.empleado, registro.fecha)
    _sincronizar_novedad(registro.empleado, registro.fecha, Tipo.ATRASO,
                         minutos_atraso(horario, registro.fecha, registro.entrada), registro)
    _sincronizar_novedad(registro.empleado, registro.fecha, Tipo.SALIDA_ANTICIPADA,
                         minutos_salida_anticipada(horario, registro.fecha, registro.salida), registro)


def _quitar_ausencia(empleado, fecha):
    ausencia = NovedadAsistencia.objects.filter(empleado=empleado, fecha=fecha, tipo=Tipo.AUSENCIA).first()
    if ausencia is None:
        return
    if ausencia.estado != Estado.PENDIENTE:
        raise ValidationError(
            f"{empleado} tiene una ausencia ya clasificada el {fecha:%d/%m/%Y}. "
            "Reabra o corrija esa novedad antes de registrar asistencia ese día."
        )
    ausencia.delete()


# --------------------------------------------------------------------------- marcaciones
def _validar_empleado(empleado):
    if empleado.estado != Empleado.Estado.ACTIVO:
        raise ValidationError(f"{empleado} no está activo ({empleado.get_estado_display().lower()}).")


@transaction.atomic
def registrar_entrada(empleado, momento: datetime, usuario=None, origen=RegistroAsistencia.Origen.SISTEMA) -> RegistroAsistencia:
    _validar_empleado(empleado)
    fecha = timezone.localtime(momento).date()
    if RegistroAsistencia.objects.filter(empleado=empleado, fecha=fecha).exists():
        raise ValidationError(f"{empleado} ya tiene la entrada registrada el {fecha:%d/%m/%Y}.")
    _quitar_ausencia(empleado, fecha)
    registro = RegistroAsistencia.objects.create(
        empleado=empleado, fecha=fecha, entrada=momento, origen=origen, registrado_por=usuario
    )
    evaluar_registro(registro)
    return registro


@transaction.atomic
def registrar_salida(empleado, momento: datetime, usuario=None) -> RegistroAsistencia:
    fecha = timezone.localtime(momento).date()
    registro = RegistroAsistencia.objects.select_for_update().filter(empleado=empleado, fecha=fecha).first()
    if registro is None:
        raise ValidationError(f"{empleado} no tiene entrada registrada el {fecha:%d/%m/%Y}; registre primero la entrada.")
    if registro.salida is not None:
        raise ValidationError(f"{empleado} ya tiene la salida registrada a las {timezone.localtime(registro.salida):%H:%M}.")
    if momento <= registro.entrada:
        raise ValidationError("La salida debe ser posterior a la entrada.")
    registro.salida = momento
    registro.save(update_fields=["salida", "actualizado_en"])
    evaluar_registro(registro)
    return registro


@transaction.atomic
def registrar_manual(empleado, fecha: date, hora_entrada: time, hora_salida: time | None,
                     usuario=None, observacion: str = "") -> RegistroAsistencia:
    """Registro o corrección de un día (para olvidos de marcación o días pasados)."""
    zona = timezone.get_current_timezone()
    entrada = timezone.make_aware(datetime.combine(fecha, hora_entrada), zona)
    salida = timezone.make_aware(datetime.combine(fecha, hora_salida), zona) if hora_salida else None
    if salida is not None and salida <= entrada:
        raise ValidationError("La hora de salida debe ser posterior a la de entrada.")
    if entrada > timezone.now():
        raise ValidationError("No se puede registrar una entrada en el futuro.")

    _quitar_ausencia(empleado, fecha)
    registro, _ = RegistroAsistencia.objects.update_or_create(
        empleado=empleado, fecha=fecha,
        defaults={"entrada": entrada, "salida": salida, "origen": RegistroAsistencia.Origen.MANUAL,
                  "registrado_por": usuario, "observacion": observacion[:255]},
    )
    evaluar_registro(registro)
    return registro


@transaction.atomic
def marcar_ausencia(empleado, fecha: date) -> NovedadAsistencia:
    if RegistroAsistencia.objects.filter(empleado=empleado, fecha=fecha).exists():
        raise ValidationError(f"{empleado} tiene asistencia registrada el {fecha:%d/%m/%Y}; no puede quedar ausente.")
    novedad, _ = NovedadAsistencia.objects.get_or_create(empleado=empleado, fecha=fecha, tipo=Tipo.AUSENCIA)
    return novedad


@transaction.atomic
def marcar_ausencias_del_dia(fecha: date) -> int:
    """Marca ausentes a los empleados activos que debían trabajar ese día y no registraron entrada."""
    if fecha > timezone.localdate():
        raise ValidationError("No se pueden marcar ausencias de días futuros.")
    con_registro = RegistroAsistencia.objects.filter(fecha=fecha).values_list("empleado_id", flat=True)
    ya_ausentes = NovedadAsistencia.objects.filter(fecha=fecha, tipo=Tipo.AUSENCIA).values_list("empleado_id", flat=True)
    creadas = 0
    for asignacion in asignaciones_vigentes(fecha).filter(empleado__estado=Empleado.Estado.ACTIVO).exclude(
            empleado_id__in=con_registro).exclude(empleado_id__in=ya_ausentes):
        if es_laborable(asignacion.horario, fecha):
            NovedadAsistencia.objects.create(empleado_id=asignacion.empleado_id, fecha=fecha, tipo=Tipo.AUSENCIA)
            creadas += 1
    return creadas


# --------------------------------------------------------------------------- clasificación
@transaction.atomic
def clasificar(novedad: NovedadAsistencia, estado: str, motivo: str, usuario, soporte=None) -> NovedadAsistencia:
    if estado not in (Estado.JUSTIFICADA, Estado.NO_JUSTIFICADA):
        raise ValidationError("Elija si la novedad es justificada o no justificada.")
    if estado == Estado.JUSTIFICADA and not motivo.strip():
        raise ValidationError("Indique el motivo de la justificación.")
    if estado == Estado.JUSTIFICADA and soporte is None and novedad.soporte_id is None:
        raise ValidationError("Para justificar debe adjuntar el archivo de respaldo (PDF, JPG o PNG).")
    novedad.estado = estado
    novedad.motivo = motivo.strip()
    novedad.revisada_por = usuario
    novedad.revisada_en = timezone.now()
    if soporte is not None:
        novedad.soporte = soporte
    novedad.save()
    return novedad


@transaction.atomic
def reabrir(novedad: NovedadAsistencia) -> NovedadAsistencia:
    novedad.estado = Estado.PENDIENTE
    novedad.revisada_por = None
    novedad.revisada_en = None
    novedad.save()
    return novedad


# --------------------------------------------------------------------------- consultas
@dataclass
class FilaDia:
    empleado: Empleado
    fecha: date
    horario: Horario | None
    registro: RegistroAsistencia | None
    novedades: list

    @property
    def tipos(self) -> set:
        return {n.tipo for n in self.novedades}

    @property
    def atraso(self):
        return next((n for n in self.novedades if n.tipo == Tipo.ATRASO), None)

    @property
    def activo(self) -> bool:
        return self.empleado.estado == Empleado.Estado.ACTIVO

    @property
    def estado(self) -> str:
        if not self.activo:
            return self.empleado.get_estado_display()
        if Tipo.AUSENCIA in self.tipos:
            return "Ausente"
        if self.registro is None:
            return "Pendiente" if es_laborable(self.horario, self.fecha) else "No laborable"
        return "Salió" if self.registro.salida else "Presente"


def tablero_del_dia(fecha: date) -> list[FilaDia]:
    empleados = Empleado.objects.exclude(estado=Empleado.Estado.DESVINCULADO).select_related("cargo").order_by("apellidos", "nombres")
    horarios = {a.empleado_id: a.horario for a in asignaciones_vigentes(fecha)}
    registros = {r.empleado_id: r for r in RegistroAsistencia.objects.filter(fecha=fecha)}
    novedades = {}
    for n in NovedadAsistencia.objects.filter(fecha=fecha):
        novedades.setdefault(n.empleado_id, []).append(n)
    return [FilaDia(e, fecha, horarios.get(e.pk), registros.get(e.pk), novedades.get(e.pk, [])) for e in empleados]


def horas_trabajadas(registro: RegistroAsistencia | None) -> timedelta | None:
    if registro is None or registro.salida is None:
        return None
    return registro.salida - registro.entrada


def historial(desde: date, hasta: date, empleado=None):
    """Filas por empleado y día con marcación o novedad, más un resumen de totales."""
    registros = RegistroAsistencia.objects.filter(fecha__range=(desde, hasta)).select_related("empleado")
    novedades = NovedadAsistencia.objects.filter(fecha__range=(desde, hasta)).select_related("empleado", "soporte")
    if empleado is not None:
        registros, novedades = registros.filter(empleado=empleado), novedades.filter(empleado=empleado)

    filas = {}
    for r in registros:
        filas[(r.fecha, r.empleado_id)] = {"fecha": r.fecha, "empleado": r.empleado, "registro": r, "novedades": []}
    for n in novedades:
        fila = filas.setdefault((n.fecha, n.empleado_id), {"fecha": n.fecha, "empleado": n.empleado, "registro": None, "novedades": []})
        fila["novedades"].append(n)

    total_horas = timedelta()
    for fila in filas.values():
        fila["horas"] = horas_trabajadas(fila["registro"])
        total_horas += fila["horas"] or timedelta()

    def contar(tipo, estado=None):
        qs = novedades.filter(tipo=tipo)
        return qs.filter(estado=estado).count() if estado else qs.count()

    resumen = {
        "dias_trabajados": registros.count(),
        "horas": total_horas,
        "atrasos": contar(Tipo.ATRASO),
        "minutos_atraso": sum(novedades.filter(tipo=Tipo.ATRASO).values_list("minutos", flat=True)),
        "salidas_anticipadas": contar(Tipo.SALIDA_ANTICIPADA),
        "ausencias": contar(Tipo.AUSENCIA),
        "ausencias_justificadas": contar(Tipo.AUSENCIA, Estado.JUSTIFICADA),
        "ausencias_no_justificadas": contar(Tipo.AUSENCIA, Estado.NO_JUSTIFICADA),
        "pendientes": novedades.filter(estado=Estado.PENDIENTE).count(),
    }
    orden = sorted(filas.values(), key=lambda f: (f["fecha"], f["empleado"].apellidos, f["empleado"].nombres), reverse=True)
    return orden, resumen


def resumen_empleado(empleado, desde: date | None = None, hasta: date | None = None) -> dict:
    """
    Récord de asistencia de una persona: faltas y atrasos con cuántos fueron justificados,
    no justificados o siguen por revisar, más el detalle de cada novedad.
    """
    from django.db.models import Count, Sum

    novedades = NovedadAsistencia.objects.filter(empleado=empleado).select_related("soporte", "revisada_por")
    registros = RegistroAsistencia.objects.filter(empleado=empleado)
    if desde:
        novedades, registros = novedades.filter(fecha__gte=desde), registros.filter(fecha__gte=desde)
    if hasta:
        novedades, registros = novedades.filter(fecha__lte=hasta), registros.filter(fecha__lte=hasta)

    def bloque(tipo):
        datos = novedades.filter(tipo=tipo).aggregate(
            total=Count("id"),
            justificadas=Count("id", filter=Q(estado=Estado.JUSTIFICADA)),
            no_justificadas=Count("id", filter=Q(estado=Estado.NO_JUSTIFICADA)),
            pendientes=Count("id", filter=Q(estado=Estado.PENDIENTE)),
            minutos=Sum("minutos"),
        )
        datos["minutos"] = datos["minutos"] or 0
        return datos

    return {
        "dias_asistidos": registros.count(),
        "faltas": bloque(Tipo.AUSENCIA),
        "atrasos": bloque(Tipo.ATRASO),
        "salidas": bloque(Tipo.SALIDA_ANTICIPADA),
        "novedades": list(novedades.order_by("-fecha", "tipo")),
    }


@transaction.atomic
def asignar_horario(horario: Horario, empleados, desde: date) -> int:
    """Asigna un horario desde una fecha; cierra la asignación anterior del empleado el día previo."""
    for empleado in empleados:
        posteriores = AsignacionHorario.objects.filter(empleado=empleado, vigente_desde__gte=desde)
        if posteriores.exists():
            raise ValidationError(f"{empleado} ya tiene un horario asignado desde el {posteriores.first().vigente_desde:%d/%m/%Y}.")
        AsignacionHorario.objects.filter(
            Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=desde), empleado=empleado, vigente_desde__lt=desde
        ).update(vigente_hasta=desde - timedelta(days=1))
        AsignacionHorario.objects.create(empleado=empleado, horario=horario, vigente_desde=desde)
    return len(empleados)
