"""4.2 Módulo de nómina: sueldos totales y roles de pago de todos los empleados."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.personal.models import Empleado, EsquemaComision
from apps.ventas.templatetags.humanize_cf import dinero

from . import caja_chica as caja
from . import calculo
from .forms import MovimientoCajaForm
from .models import MovimientoCajaChica


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
    roles = _roles_filtrados(nomina.roles, equipo)
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



def _roles_filtrados(roles, equipo: str):
    if equipo == "sin":
        return [r for r in roles if r.es_vendedor and not r.esquema]
    if equipo.isdigit():
        return [r for r in roles if r.esquema and r.esquema.pk == int(equipo)]
    return roles


# --------------------------------------------------------------------------- reporte de roles de pago (imprimir)
@login_required
@permission_required("nomina.view_periodonomina", raise_exception=True)
def reporte_roles(request):
    """Roles de pago del mes para imprimir: cuadro general con firmas, o un rol por hoja."""
    anio, mes = _mes(request)
    equipo = request.GET.get("equipo", "")
    roles = _roles_filtrados(calculo.nomina_del_mes(anio, mes).roles, equipo)
    return render(request, "nomina/reporte_roles.html", {
        "nomina": calculo.ResumenNomina(roles),
        "formato": "individual" if request.GET.get("formato") == "individual" else "general",
        "mes_consultado": f"{anio:04d}-{mes:02d}",
        "nombre_mes": calculo.nombre_mes(anio, mes),
        "equipos": EsquemaComision.objects.filter(activo=True).order_by("nombre"),
        "equipo": equipo,
        "generado_en": timezone.localtime(),
    })


# --------------------------------------------------------------------------- caja chica
def _rango(request):
    """?desde=&hasta= (AAAA-MM-DD); por defecto, del 1 del mes a hoy."""
    hoy = timezone.localdate()

    def fecha(clave, defecto):
        try:
            return parse_date(request.GET.get(clave) or "") or defecto
        except ValueError:
            return defecto

    desde, hasta = fecha("desde", hoy.replace(day=1)), fecha("hasta", hoy)
    return (desde, hasta) if desde <= hasta else (hasta, desde)


@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_chica(request):
    form = MovimientoCajaForm(request.POST or None)
    if request.method == "POST":
        if not request.user.has_perm("nomina.add_movimientocajachica"):
            return HttpResponse(status=403)
        if form.is_valid():
            try:
                m = caja.registrar(request.user, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, f"{m.get_tipo_display()} de ${dinero(m.valor)} registrado. "
                                          f"Saldo actual: ${dinero(caja.saldo())}.")
                return redirect(f"{reverse('nomina:caja_chica')}?desde={m.fecha.replace(day=1):%Y-%m-%d}"
                                f"&hasta={max(m.fecha, timezone.localdate()):%Y-%m-%d}")
    desde, hasta = _rango(request)
    return render(request, "nomina/caja_chica.html", {
        "form": form,
        "reporte": caja.reporte(desde, hasta),
        "saldo_actual": caja.saldo(),
        "desde": desde, "hasta": hasta,
    })


@login_required
@permission_required("nomina.change_movimientocajachica", raise_exception=True)
@require_POST
def caja_anular(request, pk):
    movimiento = get_object_or_404(MovimientoCajaChica, pk=pk)
    try:
        caja.anular(request.user, movimiento, request.POST.get("motivo", ""))
        messages.success(request, f"Movimiento anulado. Saldo actual: ${dinero(caja.saldo())}.")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    volver = request.POST.get("volver", "")
    if url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}):
        return redirect(volver)
    return redirect("nomina:caja_chica")


@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_reporte(request):
    desde, hasta = _rango(request)
    incluir_anulados = request.GET.get("anulados") == "1"
    return render(request, "nomina/caja_reporte.html", {
        "reporte": caja.reporte(desde, hasta, incluir_anulados=incluir_anulados),
        "desde": desde, "hasta": hasta, "incluir_anulados": incluir_anulados,
        "rango_texto": f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}",
        "generado_en": timezone.localtime(),
    })
