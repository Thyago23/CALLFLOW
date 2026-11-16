"""
Mantenimiento preventivo programado: cada equipo puede tener planes que se repiten (limpieza cada 6 meses…).

Al registrar que se hizo, queda un evento «Mantenimiento preventivo» en la hoja de vida del equipo y el plan
pasa a la siguiente fecha (fecha en que se hizo + la frecuencia). Los avisos a TI salen de apps.ti.alertas.
"""
import calendar
from datetime import date

from django.db import transaction
from django.utils import timezone

from .models import Equipo, EventoEquipo, PlanMantenimiento
from .servicios import OperacionNoPermitida


def sumar_meses(fecha: date, meses: int) -> date:
    """El mismo día `meses` después; si ese mes es más corto, el último día del mes (31/08 + 6 = 28/02)."""
    total = fecha.month - 1 + meses
    anio, mes = fecha.year + total // 12, total % 12 + 1
    return date(anio, mes, min(fecha.day, calendar.monthrange(anio, mes)[1]))


@transaction.atomic
def crear_planes(equipos, *, actividad: str, cada_meses: int, primera_fecha: date, dias_aviso: int = 7,
                 observacion: str = "", usuario=None) -> tuple[list[PlanMantenimiento], list[Equipo]]:
    """Crea el mismo plan en varios equipos. Devuelve (planes creados, equipos que ya tenían esa actividad)."""
    actividad = actividad.strip()
    existentes = set(PlanMantenimiento.objects.filter(equipo__in=equipos, actividad__iexact=actividad).values_list("equipo_id", flat=True))
    creados, omitidos = [], []
    for equipo in equipos:
        if equipo.pk in existentes:
            omitidos.append(equipo)
            continue
        creados.append(PlanMantenimiento.objects.create(
            equipo=equipo, actividad=actividad, cada_meses=cada_meses, proxima_fecha=primera_fecha, dias_aviso=dias_aviso,
            observacion=observacion, registrado_por=usuario))
    return creados, omitidos


@transaction.atomic
def registrar_realizado(plan: PlanMantenimiento, *, fecha: date, descripcion: str = "", realizado_por: str = "",
                        costo=None, usuario=None) -> EventoEquipo:
    plan = PlanMantenimiento.objects.select_for_update().select_related("equipo").get(pk=plan.pk)
    if fecha > timezone.localdate():
        raise OperacionNoPermitida("La fecha del mantenimiento no puede ser futura.")
    if plan.ultima_fecha and fecha < plan.ultima_fecha:
        raise OperacionNoPermitida(f"El último mantenimiento registrado de este plan es del {plan.ultima_fecha:%d/%m/%Y}.")
    evento = EventoEquipo.objects.create(
        equipo=plan.equipo, fecha=fecha, tipo=EventoEquipo.Tipo.MANTENIMIENTO,
        descripcion=f"{plan.actividad} (plan cada {plan.cada_meses} mes{'es' if plan.cada_meses != 1 else ''})"
                    + (f". {descripcion.strip()}" if descripcion.strip() else ""),
        realizado_por=realizado_por, costo=costo, registrado_por=usuario)
    plan.ultima_fecha = fecha
    plan.proxima_fecha = sumar_meses(fecha, plan.cada_meses)
    plan.save()
    return evento


def resumen() -> dict:
    planes = list(PlanMantenimiento.objects.filter(activo=True).exclude(equipo__estado=Equipo.Estado.BAJA))
    return {"total": len(planes), "vencidos": sum(1 for p in planes if p.situacion == "vencido"),
            "por_vencer": sum(1 for p in planes if p.situacion == "por_vencer")}
