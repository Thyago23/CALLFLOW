"""4.2 Módulo de nómina: sueldos totales y roles de pago de todos los empleados."""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core import validadores
from apps.personal.models import Empleado, EsquemaComision
from apps.reportes import formato
from apps.ventas.templatetags.humanize_cf import dinero

from . import caja_chica as caja
from . import calculo
from .forms import ArqueoCajaForm, LineaReciboForm, MovimientoCajaForm, ReciboCajaForm
from .models import DENOMINACIONES, ArqueoCaja, LineaRecibo, MovimientoCajaChica, ReciboCaja


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
    return _respuesta_pdf(calculo.rol_de_pagos_pdf(calculo.estimar_mes(empleado, anio, mes)),
                          f"Rol de pagos {anio}-{mes:02d} {empleado.apellidos} {empleado.nombres}.pdf")


@login_required
@permission_required("nomina.view_periodonomina", raise_exception=True)
def comision_pdf(request, empleado_pk):
    anio, mes = _mes(request)
    empleado = get_object_or_404(Empleado.objects.select_related("cargo"), pk=empleado_pk)
    estimacion = calculo.estimar_mes(empleado, anio, mes)
    if not estimacion.es_vendedor:
        raise Http404("Solo los vendedores tienen comisión.")
    return _respuesta_pdf(calculo.comprobante_comision_pdf(estimacion),
                          f"Comision {anio}-{mes:02d} {empleado.apellidos} {empleado.nombres}.pdf")


@login_required
@permission_required("nomina.view_periodonomina", raise_exception=True)
@permission_required("documentos.add_documento", raise_exception=True)
def acta_llamado_pdf(request, novedad_pk):
    """Acta de llamado de atención de un atraso largo; queda una copia en el expediente del empleado."""
    from apps.asistencia.models import NovedadAsistencia

    from . import llamados

    novedad = get_object_or_404(NovedadAsistencia.objects.select_related("empleado__cargo", "registro"), pk=novedad_pk)
    pdf = llamados.acta_pdf(novedad)
    with transaction.atomic():
        llamados.guardar_en_expediente(novedad, pdf, request.user)
    emp = novedad.empleado
    return _respuesta_pdf(pdf, f"Llamado de atencion {novedad.fecha:%Y-%m-%d} {emp.apellidos} {emp.nombres}.pdf")


def _respuesta_pdf(pdf: bytes, nombre: str) -> HttpResponse:
    respuesta = HttpResponse(pdf, content_type="application/pdf")
    respuesta["Content-Disposition"] = content_disposition_header(False, nombre)
    respuesta["X-Content-Type-Options"] = "nosniff"
    return respuesta


def _roles_filtrados(roles, equipo: str):
    # Sin equipo de comisiones = administrativo («sin» se acepta por los enlaces guardados de antes).
    if equipo in ("administrativos", "sin"):
        return [r for r in roles if not r.esquema]
    if equipo.isdigit():
        return [r for r in roles if r.esquema and r.esquema.pk == int(equipo)]
    return roles


# --------------------------------------------------------------------------- reporte de roles de pago (imprimir)
@login_required
@permission_required("nomina.view_periodonomina", raise_exception=True)
def reporte_roles(request):
    """Para imprimir: cuadro general con firmas, un rol por hoja, o un comprobante de comisión por vendedor."""
    anio, mes = _mes(request)
    equipo = request.GET.get("equipo", "")
    empleado = None
    if request.GET.get("empleado", "").isdigit():  # imprimir el rol de una sola persona
        empleado = get_object_or_404(Empleado.objects.select_related("cargo"), pk=int(request.GET["empleado"]))
        roles = [calculo.estimar_mes(empleado, anio, mes)]
    else:
        roles = _roles_filtrados(calculo.nomina_del_mes(anio, mes).roles, equipo)
    pedido = request.GET.get("formato")
    if pedido == "comisiones":
        formato, roles = "comisiones", [r for r in roles if r.es_vendedor]
    else:
        formato = "individual" if empleado or pedido == "individual" else "general"
    return render(request, "nomina/reporte_roles.html", {
        "nomina": calculo.ResumenNomina(roles),
        "formato": formato,
        "empleado": empleado,
        "mes_consultado": f"{anio:04d}-{mes:02d}",
        "nombre_mes": calculo.nombre_mes(anio, mes),
        "equipos": EsquemaComision.objects.filter(activo=True).order_by("nombre"),
        "equipo": equipo,
        "generado_en": timezone.localtime(),
    })


# --------------------------------------------------------------------------- caja chica
# Dos pestañas, como el Excel de la empresa: «Registro y control» (por mes) y «Arqueo y cierre».
EMPRESA = "MULTIVISAS"


def _contexto_mes(request):
    anio, mes = _mes(request) if request.method == "GET" or "mes" not in request.POST else _mes_de(request.POST.get("mes"))
    primero = date(anio, mes, 1)
    anterior = date(anio - 1, 12, 1) if mes == 1 else date(anio, mes - 1, 1)
    siguiente = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    hoy = timezone.localdate()
    return {
        "anio": anio, "mes": mes, "primero": primero, "mes_texto": calculo.nombre_mes(anio, mes),
        "mes_param": f"{anio:04d}-{mes:02d}", "mes_anterior": f"{anterior:%Y-%m}",
        "mes_siguiente": f"{siguiente:%Y-%m}" if siguiente <= hoy.replace(day=1) else "",
    }


def _mes_de(texto: str) -> tuple[int, int]:
    try:
        anio, mes = (int(x) for x in texto.split("-"))
        date(anio, mes, 1)
        return anio, mes
    except (ValueError, TypeError):
        hoy = timezone.localdate()
        return hoy.year, hoy.month


@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_chica(request):
    form = MovimientoCajaForm(request.POST or None)
    for campo in form.fields.values():  # la fila nueva vive dentro de la tabla, como en el Excel
        campo.widget.attrs["form"] = "fila-nueva"
        clases = campo.widget.attrs.get("class", "")
        campo.widget.attrs["class"] = clases.replace("form-select", "form-select form-select-sm").replace("form-control", "form-control form-control-sm")
    if request.method == "POST":
        if not request.user.has_perm("nomina.add_movimientocajachica"):
            return HttpResponse(status=403)
        if form.is_valid():
            try:
                m = caja.registrar(request.user, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, f"{m.get_tipo_display()} N.° {m.numero_recibo} de ${dinero(m.valor)} registrado. "
                                          f"Saldo actual: ${dinero(caja.saldo())}.")
                return redirect(f"{reverse('nomina:caja_chica')}?mes={m.fecha:%Y-%m}&nuevo={m.pk}")
    ctx = _contexto_mes(request)
    reporte = caja.del_mes(ctx["anio"], ctx["mes"])
    return render(request, "nomina/caja_chica.html", ctx | {
        "form": form,
        "reporte": reporte,
        "filas_vacias": range(max(0, 5 - len(reporte.filas))),
        "puede_fijar_fondo": request.user.has_perms(["nomina.add_movimientocajachica", "nomina.change_movimientocajachica"])
                             and date(ctx["anio"], ctx["mes"], 1) <= timezone.localdate(),
        "saldo_actual": caja.saldo(),
        "nuevo": request.GET.get("nuevo", ""),
        "empresa": EMPRESA,
    })


@login_required
@permission_required(["nomina.add_movimientocajachica", "nomina.change_movimientocajachica"], raise_exception=True)
@require_POST
def caja_fondo(request):
    """Celda «Fondo inicial» de la hoja del mes."""
    ctx = _contexto_mes(request)
    texto = request.POST.get("valor", "").strip().replace("$", "").replace(" ", "")
    if "," in texto:  # 1.234,56 o 170,50 (formato de Ecuador)
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(texto)
    except (ArithmeticError, ValueError):
        messages.error(request, "Escriba el fondo inicial como un número, por ejemplo 170 o 170,50.")
    else:
        try:
            rep = caja.fijar_fondo_inicial(request.user, ctx["anio"], ctx["mes"], valor)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, f"Fondo inicial de {ctx['mes_texto']}: ${dinero(rep.fondo_inicial)}. "
                                      f"Saldo actual: ${dinero(rep.saldo_final)}.")
    return redirect(f"{reverse('nomina:caja_chica')}?mes={ctx['mes_param']}")


@login_required
@permission_required("nomina.change_movimientocajachica", raise_exception=True)
@require_POST
def caja_anular(request, pk):
    movimiento = get_object_or_404(MovimientoCajaChica, pk=pk)
    try:
        caja.anular(request.user, movimiento, request.POST.get("motivo", ""))
        messages.success(request, f"Movimiento N.° {movimiento.numero_recibo} anulado. Saldo actual: ${dinero(caja.saldo())}.")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    volver = request.POST.get("volver", "")
    if url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}):
        return redirect(volver)
    return redirect("nomina:caja_chica")


@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_reporte(request):
    """Hoja «Registro y control de caja chica» del mes, para imprimir (A4 horizontal)."""
    ctx = _contexto_mes(request)
    incluir_anulados = request.GET.get("anulados") == "1"
    reporte = caja.del_mes(ctx["anio"], ctx["mes"], incluir_anulados=incluir_anulados)
    return render(request, "nomina/caja_reporte.html", ctx | {
        "reporte": reporte,
        "filas_vacias": range(max(0, 12 - len(reporte.filas))),  # filas en blanco para anotar a mano, como la hoja
        "incluir_anulados": incluir_anulados, "empresa": EMPRESA,
        "generado_en": timezone.localtime(),
    })


@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_recibo(request, pk):
    """
    Recibo de un gasto de la hoja con el formato «Recibo Caja Chica» (una fila: lo entregado y lo gastado).
    Si el movimiento salió de un recibo, se abre ese recibo.
    """
    m = get_object_or_404(MovimientoCajaChica.objects.select_related("registrado_por", "anulado_por"), pk=pk)
    if m.recibo_id:
        return redirect("nomina:caja_recibo_entrega", pk=m.recibo_id)
    if m.tipo != MovimientoCajaChica.Tipo.EGRESO:
        raise Http404("Solo los egresos tienen recibo.")
    responsable = m.registrado_por
    linea = ReciboDeGasto.Linea(m.fecha, m.concepto, m.comprobante, m.valor, m.valor, m.anulado)
    recibo = ReciboDeGasto(
        numero=f"M-{m.numero_recibo}", fecha=m.fecha, entregado_a=m.beneficiario, area="", autorizado_por="",
        responsable=responsable, observaciones=(f"ANULADO: {m.motivo_anulacion}" if m.anulado else ""),
        lineas_vista=[linea], total_entregado=m.valor if not m.anulado else Decimal("0"),
        total_gastado=m.valor if not m.anulado else Decimal("0"),
    )
    return render(request, "nomina/caja_recibo.html", {"r": recibo, "m": m, "empresa": EMPRESA, "editable": False,
                                                       "filas_vacias": range(0), "primera_vacia": 0, "volver": f"{reverse('nomina:caja_chica')}?mes={m.fecha:%Y-%m}"})


class ReciboDeGasto:
    """Lo que necesita la plantilla del recibo para mostrar un gasto suelto de la hoja."""

    class Linea:
        def __init__(self, fecha, concepto, comprobante, valor_entregado, valor_gastado, anulada):
            self.fecha, self.concepto, self.comprobante = fecha, concepto, comprobante
            self.valor_entregado, self.valor_gastado, self.anulada = valor_entregado, valor_gastado, anulada

    def __init__(self, *, lineas_vista, total_entregado, total_gastado, **datos):
        self.__dict__.update(datos)
        self.lineas_vista = lineas_vista
        self.total_entregado, self.total_gastado = total_entregado, total_gastado
        self.valor_a_devolver = max(total_entregado - total_gastado, Decimal("0"))
        self.valor_a_reembolsar = max(total_gastado - total_entregado, Decimal("0"))
        self.valor_devuelto = Decimal("0")
        self.saldo_pendiente = self.valor_a_devolver
        self.abierto = False
        self.liquidado = True


# --------------------------------------------------------------------------- recibos (entregas a rendir)
@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_recibos(request):
    """Pestaña «Recibos»: lista de recibos y uno nuevo."""
    form = ReciboCajaForm(request.POST or None)
    if request.method == "POST":
        if not request.user.has_perm("nomina.add_movimientocajachica"):
            return HttpResponse(status=403)
        if form.is_valid():
            try:
                recibo = caja.crear_recibo(request.user, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, f"Recibo N.º {recibo.numero} creado. Registre lo entregado y lo gastado en la tabla.")
                return redirect("nomina:caja_recibo_entrega", pk=recibo.pk)
    recibos = ReciboCaja.objects.select_related("responsable").prefetch_related("lineas")[:50]
    return render(request, "nomina/caja_recibos.html", {"form": form, "recibos": recibos, "saldo_actual": caja.saldo()})


@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_recibo_entrega(request, pk):
    """Un recibo con el formato del Excel: se llena en pantalla y se imprime igual."""
    recibo = get_object_or_404(ReciboCaja.objects.select_related("responsable"), pk=pk)
    form_linea = LineaReciboForm(request.POST if request.POST.get("accion") == "linea" else None)
    form_datos = ReciboCajaForm(request.POST if request.POST.get("accion") == "datos" else None, prefix="datos", initial={
        "fecha": recibo.fecha, "entregado_a": recibo.entregado_a, "area": recibo.area, "autorizado_por": recibo.autorizado_por,
        "observaciones": recibo.observaciones})
    if request.method == "POST":
        if not request.user.has_perms(["nomina.add_movimientocajachica", "nomina.change_movimientocajachica"]):
            return HttpResponse(status=403)
        accion = request.POST.get("accion")
        try:
            if accion == "linea" and form_linea.is_valid():
                caja.agregar_linea(request.user, recibo, **form_linea.cleaned_data)
                messages.success(request, f"Fila agregada. Saldo de la caja: ${dinero(caja.saldo())}.")
                return redirect("nomina:caja_recibo_entrega", pk=recibo.pk)
            if accion == "datos" and form_datos.is_valid():
                caja.actualizar_recibo(recibo, **form_datos.cleaned_data)
                messages.success(request, "Datos del recibo guardados.")
                return redirect("nomina:caja_recibo_entrega", pk=recibo.pk)
            if accion == "quitar":
                linea = get_object_or_404(LineaRecibo, pk=validadores.entero(request.POST.get("linea")), recibo=recibo)
                caja.anular_linea(request.user, linea)
                messages.success(request, "Fila quitada del recibo." + (" Lo entregado volvió a la caja." if linea.movimiento_id else ""))
                return redirect("nomina:caja_recibo_entrega", pk=recibo.pk)
            if accion in ("liquidar", "devolucion"):
                valor = _decimal_de(request.POST.get("valor_devuelto", ""))
                if accion == "liquidar":
                    caja.liquidar_recibo(request.user, recibo, valor)
                    messages.success(request, f"Recibo N.º {recibo.numero} liquidado. Saldo de la caja: ${dinero(caja.saldo())}.")
                else:
                    caja.registrar_devolucion(request.user, recibo, valor)
                    messages.success(request, f"Devolución registrada. Saldo pendiente: ${dinero(recibo.saldo_pendiente)}.")
                return redirect("nomina:caja_recibo_entrega", pk=recibo.pk)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            if accion != "linea":
                return redirect("nomina:caja_recibo_entrega", pk=recibo.pk)
    lineas = list(recibo.lineas.all())
    recibo.lineas_vista = [l for l in lineas if not l.anulada]
    editable = recibo.abierto and request.user.has_perms(["nomina.add_movimientocajachica", "nomina.change_movimientocajachica"])
    return render(request, "nomina/caja_recibo.html", {
        "r": recibo, "empresa": EMPRESA, "form_linea": form_linea, "form_datos": form_datos,
        "editable": editable, "primera_vacia": 1 if editable else 0,
        "puede_devolver": not recibo.abierto and recibo.saldo_pendiente > 0
                          and request.user.has_perm("nomina.add_movimientocajachica"),
        "filas_vacias": range(0),  # como en el Excel en pantalla: solo las filas con datos
        "anuladas": [l for l in lineas if l.anulada],
        "volver": reverse("nomina:caja_recibos"),
    })


def _decimal_de(texto: str):
    """«170», «170,50», «1.234,56» o «$ 20» → Decimal; vacío → 0."""
    texto = (texto or "").strip().replace("$", "").replace(" ", "")
    if not texto:
        return Decimal("0")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto)
    except ArithmeticError as error:
        raise ValidationError("Escriba el valor como un número, por ejemplo 20 o 20,50.") from error


@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_arqueo(request):
    """Pestaña «Arqueo y cierre»: contar el efectivo y compararlo con el registro."""
    if request.method == "GET" and request.GET.get("saldo_al"):  # el saldo según registro cambia con la fecha del arqueo
        try:
            fecha = date.fromisoformat(request.GET["saldo_al"])
        except ValueError:
            return JsonResponse({"error": "Fecha no válida."}, status=400)
        return JsonResponse({"saldo": str(caja.saldo(hasta=fecha))})
    form = ArqueoCajaForm(request.POST or None)
    cantidades = {d: request.POST.get(f"cantidad_{d}", "") for d in DENOMINACIONES} if request.method == "POST" else {}
    if request.method == "POST":
        if not request.user.has_perm("nomina.add_movimientocajachica"):
            return HttpResponse(status=403)
        if form.is_valid():
            try:
                arqueo = caja.registrar_arqueo(request.user, cantidades=cantidades, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if arqueo.cuadra:
                    messages.success(request, "Arqueo registrado: la caja cuadra con el registro.")
                else:
                    messages.warning(request, f"Arqueo registrado con una diferencia de {formato.dinero(arqueo.diferencia)}.")
                return redirect("nomina:caja_arqueo_detalle", pk=arqueo.pk)
    return render(request, "nomina/caja_arqueo.html", {
        "form": form,
        "filas": [{"clave": d, "valor": Decimal(d), "cantidad": cantidades.get(d, "")} for d in DENOMINACIONES],
        "saldo_actual": caja.saldo(),
        "saldo_registro": caja.saldo(hasta=(form.cleaned_data.get("fecha") if form.is_bound and form.is_valid() else None)
                                     or timezone.localdate()),
        "empresa": EMPRESA,
        "arqueos": ArqueoCaja.objects.select_related("responsable")[:12],
    })


@login_required
@permission_required("nomina.view_movimientocajachica", raise_exception=True)
def caja_arqueo_detalle(request, pk):
    """Hoja «Arqueo y cierre de caja chica» para imprimir (A4 vertical)."""
    arqueo = get_object_or_404(ArqueoCaja.objects.select_related("responsable", "registrado_por"), pk=pk)
    return render(request, "nomina/caja_arqueo_detalle.html", {
        "a": arqueo, "empresa": EMPRESA, "generado_en": timezone.localtime(),
    })
