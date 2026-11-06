"""4.2 Módulo de nómina: sueldos totales y roles de pago de todos los empleados."""
from datetime import date

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.http import content_disposition_header

from apps.personal.models import Empleado, EsquemaComision

from . import calculo


def _mes(request) -> tuple[int, int]:
    """?mes=2026-11 → (2026, 11); por defecto, el mes actual."""
    hoy = timezone.localdate()
    try:
        anio, mes = (int(x) for x in request.GET.get("mes", "").split("-"))
        date(anio, mes, 1)
        return anio, mes
    except (ValueError, TypeError):
        return hoy.year, hoy.month


@login_required
@permission_required("nomina.view_periodonomina", raise_exception=True)
def resumen(request):
    anio, mes = _mes(request)
    nomina = calculo.nomina_del_mes(anio, mes)
    equipo = request.GET.get("equipo", "")
    texto = request.GET.get("q", "").strip().lower()
    roles = nomina.roles
    if equipo == "sin":
        roles = [r for r in roles if r.es_vendedor and not r.esquema]
    elif equipo.isdigit():
        roles = [r for r in roles if r.esquema and r.esquema.pk == int(equipo)]
    if texto:
        roles = [r for r in roles if texto in f"{r.empleado.nombres} {r.empleado.apellidos} {r.empleado.identificacion}".lower()]
    return render(request, "nomina/resumen.html", {
        "nomina": nomina,  # los totales siempre son de toda la empresa
        "roles": roles,
        "mes_consultado": f"{anio:04d}-{mes:02d}",
        "nombre_mes": calculo.nombre_mes(anio, mes),
        "equipos": EsquemaComision.objects.filter(activo=True).order_by("nombre"),
        "equipo": equipo,
        "q": request.GET.get("q", ""),
    })


@login_required
@permission_required("nomina.view_periodonomina", raise_exception=True)
def rol(request, empleado_pk):
    anio, mes = _mes(request)
    empleado = get_object_or_404(Empleado.objects.select_related("cargo"), pk=empleado_pk)
    return render(request, "nomina/rol.html", {
        "e": calculo.estimar_mes(empleado, anio, mes),
        "mes_consultado": f"{anio:04d}-{mes:02d}",
        "nombre_mes": calculo.nombre_mes(anio, mes),
    })


@login_required
@permission_required("nomina.view_periodonomina", raise_exception=True)
def rol_pdf(request, empleado_pk):
    anio, mes = _mes(request)
    empleado = get_object_or_404(Empleado.objects.select_related("cargo"), pk=empleado_pk)
    pdf = calculo.rol_de_pagos_pdf(calculo.estimar_mes(empleado, anio, mes))
    respuesta = HttpResponse(pdf, content_type="application/pdf")
    nombre = f"Rol de pagos {anio}-{mes:02d} {empleado.apellidos} {empleado.nombres}.pdf"
    respuesta["Content-Disposition"] = content_disposition_header(False, nombre)
    respuesta["X-Content-Type-Options"] = "nosniff"
    return respuesta
