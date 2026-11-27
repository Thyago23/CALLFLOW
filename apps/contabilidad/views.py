"""Contabilidad: inicio, caja y bancos, plan de cuentas, libro diario, saldos iniciales, balance, mayor y activos fijos."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import empresa as empresas
from . import servicios
from .empresa import con_empresa
from .forms import (ActivoFijoForm, AsientoForm, CuentaContableForm, CuentaFinancieraForm, LineaFormSet, MotivoForm,
                    MovimientoForm, TraspasoForm, iniciales_de_lineas, terceros_de)
from .models import ActivoFijo, Asiento, CuentaContable, CuentaFinanciera, Empresa, MovimientoFinanciero


def _rango(request):
    return parse_date(request.GET.get("desde") or ""), parse_date(request.GET.get("hasta") or "")


def _fechas(desde=None, hasta=None) -> str:
    """Querystring con el período, para que los enlaces al mayor lo conserven."""
    return "&".join(f"{k}={v:%Y-%m-%d}" for k, v in (("desde", desde), ("hasta", hasta)) if v)


def _errores(form, error: ValidationError):
    if hasattr(error, "error_dict"):
        for campo, lista in error.error_dict.items():
            form.add_error(campo if campo in form.fields else None, lista)
    else:
        form.add_error(None, error)


# --------------------------------------------------------------------------- inicio y empresa
@con_empresa("contabilidad.view_asiento")
def inicio(request):
    return render(request, "contabilidad/inicio.html", {"r": servicios.resumen(request.empresa), "seccion": "inicio"})


@con_empresa("contabilidad.view_asiento")
@require_POST
def cambiar_empresa(request):
    elegida = get_object_or_404(Empresa, pk=request.POST.get("empresa") or 0, activa=True)
    empresas.fijar(request, elegida)
    messages.info(request, f"Ahora está trabajando en {elegida}.")
    destino = request.POST.get("volver", "")
    # Solo se vuelve a una lista: el detalle de un registro de la otra empresa daría «no encontrado».
    return redirect(destino if destino.startswith(("/contabilidad/", "/crm/")) and destino.count("/") <= 3
                    else "contabilidad:inicio")


# --------------------------------------------------------------------------- caja y bancos
@con_empresa("contabilidad.view_cuentafinanciera")
def cajas(request):
    return render(request, "contabilidad/cajas.html", {
        "saldos": servicios.saldos_por_cuenta(request.empresa), "seccion": "cajas"})


def _form_caja(request, cuenta=None):
    form = CuentaFinancieraForm(request.POST or None, instance=cuenta, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        nueva = form.save(commit=False)
        nueva.empresa = request.empresa
        try:
            servicios._guardar(nueva)
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, f"«{nueva.nombre}» guardada.")
            return redirect("contabilidad:caja_detalle", pk=nueva.pk)
    return render(request, "contabilidad/formulario.html", {
        "form": form, "seccion": "cajas", "titulo": "Editar caja o banco" if cuenta else "Nueva caja o banco",
        "volver": reverse("contabilidad:caja_detalle", args=[cuenta.pk]) if cuenta else reverse("contabilidad:cajas"),
        "secciones": [
            ("Cuenta", "bank", "Una caja (efectivo) o una cuenta bancaria de la empresa.",
             ["nombre", "tipo", "banco", "numero", "cuenta_contable", "activa"]),
            ("Saldo inicial", "flag", "Desde aquí se calcula el saldo. Los movimientos no pueden ser anteriores a esta fecha.",
             ["saldo_inicial", "fecha_saldo_inicial"]),
        ]})


@con_empresa("contabilidad.add_cuentafinanciera")
def caja_crear(request):
    return _form_caja(request)


@con_empresa("contabilidad.change_cuentafinanciera")
def caja_editar(request, pk):
    return _form_caja(request, get_object_or_404(CuentaFinanciera, pk=pk, empresa=request.empresa))


@con_empresa("contabilidad.view_cuentafinanciera")
def caja_detalle(request, pk):
    cuenta = get_object_or_404(CuentaFinanciera.objects.select_related("cuenta_contable"), pk=pk, empresa=request.empresa)
    desde, hasta = _rango(request)
    anterior, filas, final = servicios.estado_de_cuenta(cuenta, desde, hasta)
    return render(request, "contabilidad/caja_detalle.html", {
        "cuenta": cuenta, "anterior": anterior, "filas": filas, "final": final, "desde": desde, "hasta": hasta,
        "saldo": servicios.saldo(cuenta), "seccion": "cajas", "motivo_form": MotivoForm()})


@con_empresa("contabilidad.view_movimientofinanciero")
def movimientos(request):
    qs = (MovimientoFinanciero.objects.filter(cuenta__empresa=request.empresa)
          .select_related("cuenta", "cliente", "proveedor").order_by("-fecha", "-pk"))
    cuenta, tipo, estado = request.GET.get("cuenta", ""), request.GET.get("tipo", ""), request.GET.get("estado", "")
    texto = request.GET.get("q", "").strip()
    if cuenta.isdigit():
        qs = qs.filter(cuenta_id=int(cuenta))
    if tipo in MovimientoFinanciero.Tipo.values:
        qs = qs.filter(tipo=tipo)
    if estado == "sin_conciliar":
        qs = qs.filter(anulado=False, conciliado=False)
    elif estado == "conciliados":
        qs = qs.filter(conciliado=True)
    elif estado == "anulados":
        qs = qs.filter(anulado=True)
    if texto:
        qs = qs.filter(Q(concepto__icontains=texto) | Q(referencia__icontains=texto) | Q(tercero__icontains=texto)
                       | Q(cliente__nombres__icontains=texto) | Q(proveedor__nombre__icontains=texto))
    desde, hasta = _rango(request)
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    vigentes = qs.filter(anulado=False)
    totales = {
        "entradas": vigentes.filter(tipo__in=MovimientoFinanciero.ENTRADAS).aggregate(t=Sum("valor"))["t"] or 0,
        "salidas": vigentes.filter(tipo__in=MovimientoFinanciero.SALIDAS).aggregate(t=Sum("valor"))["t"] or 0,
    }
    return render(request, "contabilidad/movimientos.html", {
        "movimientos": qs[:500], "totales": totales, "cuenta": cuenta, "tipo": tipo, "estado": estado, "q": texto,
        "desde": desde, "hasta": hasta, "tipos": MovimientoFinanciero.Tipo.choices, "seccion": "movimientos",
        "cuentas": CuentaFinanciera.objects.filter(empresa=request.empresa).order_by("tipo", "nombre")})


@con_empresa("contabilidad.add_movimientofinanciero")
def movimiento_crear(request):
    inicial = {}
    if request.GET.get("cuenta", "").isdigit():
        inicial["cuenta"] = int(request.GET["cuenta"])
    if request.GET.get("tipo") in MovimientoFinanciero.Tipo.values:
        inicial["tipo"] = request.GET["tipo"]
    if request.GET.get("cliente", "").isdigit():
        inicial["cliente"] = int(request.GET["cliente"])
    form = MovimientoForm(request.POST or None, request.FILES or None, initial=inicial, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            movimiento = servicios.registrar_movimiento(
                request.user, cuenta=d["cuenta"], fecha=d["fecha"], tipo=d["tipo"], concepto=d["concepto"], valor=d["valor"],
                cliente=d["cliente"], proveedor=d["proveedor"], tercero=d["tercero"], referencia=d["referencia"],
                observaciones=d["observaciones"], soporte=d.get("soporte"), soporte_mime=getattr(form, "soporte_mime", ""))
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, f"{movimiento.get_tipo_display()} de ${movimiento.valor:,.2f} registrado en "
                                      f"{movimiento.cuenta}. Saldo: ${servicios.saldo(movimiento.cuenta):,.2f}.")
            return redirect("contabilidad:caja_detalle", pk=movimiento.cuenta_id)
    return render(request, "contabilidad/movimiento_form.html", {"form": form, "seccion": "movimientos"})


@con_empresa("contabilidad.add_movimientofinanciero")
def traspaso_crear(request):
    form = TraspasoForm(request.POST or None, empresa=request.empresa,
                        initial={"origen": request.GET.get("origen")} if request.GET.get("origen", "").isdigit() else None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            salida = servicios.registrar_traspaso(request.user, origen=d["origen"], destino=d["destino"], fecha=d["fecha"],
                                                  valor=d["valor"], concepto=d["concepto"], referencia=d["referencia"])
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, f"Traspaso de ${salida.valor:,.2f} de {d['origen']} a {d['destino']} registrado.")
            return redirect("contabilidad:caja_detalle", pk=salida.cuenta_id)
    return render(request, "contabilidad/formulario.html", {
        "form": form, "seccion": "movimientos", "titulo": "Traspaso entre cuentas propias",
        "volver": reverse("contabilidad:movimientos"), "boton": "Registrar traspaso",
        "secciones": [("Traspaso", "arrow-left-right",
                       "Sale de una cuenta y entra en la otra por el mismo valor. Ej.: depositar el efectivo de la caja en el banco.",
                       ["origen", "destino", "fecha", "valor", "concepto", "referencia"])]})


def _movimiento(request, pk):
    return get_object_or_404(MovimientoFinanciero.objects.select_related("cuenta"), pk=pk, cuenta__empresa=request.empresa)


@con_empresa("contabilidad.change_movimientofinanciero")
@require_POST
def movimiento_anular(request, pk):
    movimiento = _movimiento(request, pk)
    try:
        servicios.anular_movimiento(request.user, movimiento, request.POST.get("motivo", ""))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Movimiento anulado: ya no cuenta en el saldo." +
                         (" También se anuló el otro lado del traspaso." if movimiento.es_traspaso else ""))
    return redirect("contabilidad:caja_detalle", pk=movimiento.cuenta_id)


@con_empresa("contabilidad.change_movimientofinanciero")
@require_POST
def movimiento_conciliar(request, pk):
    movimiento = _movimiento(request, pk)
    try:
        servicios.conciliar(movimiento, request.POST.get("conciliado") == "1", parse_date(request.POST.get("fecha") or ""))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    destino = request.POST.get("volver", "")
    return redirect(destino if destino.startswith("/contabilidad/") else
                    reverse("contabilidad:caja_detalle", args=[movimiento.cuenta_id]))


@con_empresa("contabilidad.view_movimientofinanciero")
def movimiento_soporte(request, pk):
    """El soporte no es público: solo quien ve la contabilidad de esa empresa."""
    movimiento = _movimiento(request, pk)
    if not movimiento.soporte:
        raise Http404("El movimiento no tiene soporte.")
    extension = movimiento.soporte.name.rsplit(".", 1)[-1]
    respuesta = FileResponse(movimiento.soporte.open("rb"), content_type=movimiento.soporte_mime or "application/octet-stream")
    respuesta["Content-Disposition"] = f'inline; filename="soporte_{movimiento.pk}.{extension}"'
    return respuesta


# --------------------------------------------------------------------------- plan de cuentas
@con_empresa("contabilidad.view_cuentacontable")
def plan_cuentas(request):
    qs = CuentaContable.objects.order_by("codigo")
    texto, tipo = request.GET.get("q", "").strip(), request.GET.get("tipo", "")
    if texto:
        qs = qs.filter(Q(codigo__startswith=texto) | Q(nombre__icontains=texto))
    if tipo in CuentaContable.Tipo.values:
        qs = qs.filter(tipo=tipo)
    codigos = set(CuentaContable.objects.values_list("codigo", flat=True))
    padres = {c.rpartition(".")[0] for c in codigos}
    cuentas = [(c, c.codigo not in padres) for c in qs]
    return render(request, "contabilidad/plan_cuentas.html", {
        "cuentas": cuentas, "q": texto, "tipo": tipo, "tipos": CuentaContable.Tipo.choices, "seccion": "plan"})


def _form_cuenta(request, cuenta=None):
    inicial = {}
    if not cuenta and request.GET.get("padre"):
        padre = CuentaContable.objects.filter(codigo=request.GET["padre"]).first()
        if padre:
            inicial = {"codigo": f"{padre.codigo}.", "tipo": padre.tipo, "naturaleza": padre.naturaleza}
    form = CuentaContableForm(request.POST or None, instance=cuenta, initial=inicial)
    if request.method == "POST" and form.is_valid():
        guardada = form.save()
        messages.success(request, f"Cuenta {guardada} guardada.")
        return redirect(f"{reverse('contabilidad:plan_cuentas')}?q={guardada.codigo}")
    return render(request, "contabilidad/formulario.html", {
        "form": form, "seccion": "plan", "titulo": "Editar cuenta" if cuenta else "Nueva cuenta",
        "volver": reverse("contabilidad:plan_cuentas"),
        "secciones": [("Cuenta contable", "list-ol",
                       "El plan es el mismo para Multivisas y T&T. Solo las cuentas sin subcuentas reciben valores en los asientos.",
                       ["codigo", "nombre", "tipo", "naturaleza", "descripcion", "activa"])]})


@con_empresa("contabilidad.add_cuentacontable")
def cuenta_crear(request):
    return _form_cuenta(request)


@con_empresa("contabilidad.change_cuentacontable")
def cuenta_editar(request, pk):
    return _form_cuenta(request, get_object_or_404(CuentaContable, pk=pk))


# --------------------------------------------------------------------------- libro diario
@con_empresa("contabilidad.view_asiento")
def diario(request):
    qs = (Asiento.objects.filter(empresa=request.empresa).annotate(total=Sum("lineas__debe"), n_lineas=Count("lineas"))
          .order_by("-fecha", "-numero", "-pk"))
    estado, tipo, texto = request.GET.get("estado", ""), request.GET.get("tipo", ""), request.GET.get("q", "").strip()
    if estado in Asiento.Estado.values:
        qs = qs.filter(estado=estado)
    if tipo in Asiento.Tipo.values:
        qs = qs.filter(tipo=tipo)
    if texto:
        filtro = Q(concepto__icontains=texto) | Q(documento__icontains=texto)
        if texto.isdigit():
            filtro |= Q(numero=int(texto))
        qs = qs.filter(filtro)
    desde, hasta = _rango(request)
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    return render(request, "contabilidad/diario.html", {
        "asientos": qs[:500], "estado": estado, "tipo": tipo, "q": texto, "desde": desde, "hasta": hasta,
        "estados": Asiento.Estado.choices, "tipos": Asiento.Tipo.choices, "seccion": "diario"})


def _form_asiento(request, asiento: Asiento):
    nuevo = asiento.pk is None
    cuentas = servicios.cuentas_de_movimiento()
    terceros = terceros_de(request.empresa)
    form = AsientoForm(request.POST or None, instance=asiento)
    inicial = iniciales_de_lineas(asiento) if not nuevo else None
    lineas = LineaFormSet(request.POST or None, initial=inicial, prefix="lineas",
                          form_kwargs={"cuentas": cuentas, "terceros": terceros})
    if request.method == "POST" and form.is_valid() and lineas.is_valid():
        asiento = form.save(commit=False)
        asiento.empresa = request.empresa
        guardado = False
        try:
            servicios.guardar_borrador(asiento, lineas.lineas(request.empresa), request.user)
            guardado = True
            if request.POST.get("accion") == "contabilizar":
                servicios.contabilizar(asiento, request.user)
        except ValidationError as error:
            if not guardado and nuevo:
                asiento.pk = None  # se deshizo el guardado: el formulario sigue siendo de un asiento nuevo
            if guardado and request.POST.get("accion") == "contabilizar":
                messages.warning(request, "Se guardó como borrador, pero no se pudo contabilizar: " + " ".join(error.messages))
                return redirect("contabilidad:asiento_detalle", pk=asiento.pk)
            _errores(form, error)
        else:
            asiento.refresh_from_db()
            if asiento.es_borrador:
                messages.success(request, "Asiento guardado como borrador.")
            else:
                messages.success(request, f"Asiento N.º {asiento.numero} contabilizado.")
            return redirect("contabilidad:asiento_detalle", pk=asiento.pk)
    return render(request, "contabilidad/asiento_form.html", {
        "form": form, "lineas": lineas, "asiento": asiento, "nuevo": nuevo, "seccion": "diario",
        "es_apertura": asiento.tipo == Asiento.Tipo.APERTURA})


@con_empresa("contabilidad.add_asiento")
def asiento_crear(request):
    asiento = Asiento(empresa=request.empresa)
    if request.GET.get("tipo") == Asiento.Tipo.APERTURA:
        if servicios.apertura_de(request.empresa):
            return redirect("contabilidad:saldos_iniciales")
        asiento.tipo, asiento.concepto = Asiento.Tipo.APERTURA, "Saldos iniciales"
    return _form_asiento(request, asiento)


@con_empresa("contabilidad.change_asiento")
def asiento_editar(request, pk):
    asiento = get_object_or_404(Asiento, pk=pk, empresa=request.empresa)
    if not asiento.es_borrador:
        messages.info(request, "El asiento ya está contabilizado: no se modifica (se puede anular).")
        return redirect("contabilidad:asiento_detalle", pk=asiento.pk)
    return _form_asiento(request, asiento)


@con_empresa("contabilidad.view_asiento")
def asiento_detalle(request, pk):
    asiento = get_object_or_404(Asiento.objects.select_related("registrado_por", "contabilizado_por", "anulado_por"),
                                pk=pk, empresa=request.empresa)
    lineas = asiento.lineas.select_related("cuenta", "cliente", "proveedor")
    debe, haber = servicios.totales(asiento)
    return render(request, "contabilidad/asiento_detalle.html", {
        "asiento": asiento, "lineas": lineas, "debe": debe, "haber": haber, "diferencia": debe - haber,
        "seccion": "saldos" if asiento.tipo == Asiento.Tipo.APERTURA else "diario", "motivo_form": MotivoForm()})


@con_empresa("contabilidad.change_asiento")
@require_POST
def asiento_contabilizar(request, pk):
    asiento = get_object_or_404(Asiento, pk=pk, empresa=request.empresa)
    try:
        asiento = servicios.contabilizar(asiento, request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, f"Asiento N.º {asiento.numero} contabilizado.")
    return redirect("contabilidad:asiento_detalle", pk=pk)


@con_empresa("contabilidad.change_asiento")
@require_POST
def asiento_anular(request, pk):
    asiento = get_object_or_404(Asiento, pk=pk, empresa=request.empresa)
    try:
        servicios.anular_asiento(asiento, request.user, request.POST.get("motivo", ""))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, f"Asiento N.º {asiento.numero} anulado: ya no cuenta en el balance.")
    return redirect("contabilidad:asiento_detalle", pk=pk)


@con_empresa("contabilidad.delete_asiento")
@require_POST
def asiento_borrar(request, pk):
    asiento = get_object_or_404(Asiento, pk=pk, empresa=request.empresa)
    try:
        servicios.borrar_borrador(asiento)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("contabilidad:asiento_detalle", pk=pk)
    messages.success(request, "Borrador eliminado.")
    return redirect("contabilidad:diario")


@con_empresa("contabilidad.view_asiento")
def saldos_iniciales(request):
    """Los saldos iniciales son el asiento de apertura de la empresa (hoja SALDOS_INICIALES)."""
    apertura = servicios.apertura_de(request.empresa)
    if apertura:
        return redirect("contabilidad:asiento_detalle", pk=apertura.pk)
    return render(request, "contabilidad/saldos_iniciales.html", {
        "seccion": "saldos", "cajas": CuentaFinanciera.objects.filter(empresa=request.empresa)})


# --------------------------------------------------------------------------- balance y mayor
@con_empresa("contabilidad.view_asiento")
def balance(request):
    desde, hasta = _rango(request)
    filas, total_debe, total_haber = servicios.balance_comprobacion(request.empresa, desde, hasta)
    nivel = request.GET.get("nivel", "")
    if nivel.isdigit():
        filas = [f for f in filas if f.nivel <= int(nivel)]
    hojas = [f for f in filas if not any(o.cuenta.codigo.startswith(f"{f.cuenta.codigo}.") for o in filas)]
    return render(request, "contabilidad/balance.html", {
        "filas": filas, "total_debe": total_debe, "total_haber": total_haber, "cuadra": total_debe == total_haber,
        "total_deudor": sum(f.saldo_deudor for f in hojas), "total_acreedor": sum(f.saldo_acreedor for f in hojas),
        "desde": desde, "hasta": hasta, "nivel": nivel, "seccion": "balance",
        "borradores": Asiento.objects.filter(empresa=request.empresa, estado=Asiento.Estado.BORRADOR).count()})


@con_empresa("contabilidad.view_asiento")
def mayor(request, pk):
    cuenta = get_object_or_404(CuentaContable, pk=pk)
    desde, hasta = _rango(request)
    anterior, filas, final = servicios.libro_mayor(request.empresa, cuenta, desde, hasta)
    return render(request, "contabilidad/mayor.html", {
        "cuenta": cuenta, "anterior": anterior, "filas": filas, "final": final, "desde": desde, "hasta": hasta,
        "seccion": "mayor"})


@con_empresa("contabilidad.view_asiento")
def mayor_inicio(request):
    """Libro mayor: se elige la cuenta; abajo, las cuentas con movimientos en el período."""
    desde, hasta = _rango(request)
    fechas = _fechas(desde, hasta)
    elegida = request.GET.get("cuenta", "")
    if elegida.isdigit():
        cuenta = get_object_or_404(CuentaContable, pk=elegida)
        return redirect(reverse("contabilidad:mayor", args=[cuenta.pk]) + (f"?{fechas}" if fechas else ""))
    filas, _, _ = servicios.balance_comprobacion(request.empresa, desde, hasta)
    codigos = [f.cuenta.codigo for f in filas]
    con_movimiento = [(f, servicios._con_naturaleza(f.cuenta, f.debe, f.haber)) for f in filas
                      if not any(c.startswith(f"{f.cuenta.codigo}.") for c in codigos)]
    cuentas = sorted(CuentaContable.objects.filter(activa=True), key=lambda c: servicios.orden_codigo(c.codigo))
    return render(request, "contabilidad/mayor_inicio.html", {
        "cuentas": cuentas, "con_movimiento": con_movimiento, "desde": desde, "hasta": hasta, "fechas": fechas,
        "seccion": "mayor"})


def _nivel(request):
    nivel = request.GET.get("nivel", "")
    return nivel if nivel in ("1", "2", "3") else ""


def _filtrar_nivel(seccion, nivel: str):
    if nivel:
        seccion.filas = [f for f in seccion.filas if f.nivel <= int(nivel)]
    return seccion


@con_empresa("contabilidad.view_asiento")
def estado_resultados(request):
    desde, hasta = _rango(request)
    nivel = _nivel(request)
    estado = servicios.estado_resultados(request.empresa, desde, hasta)
    for clave in ("ingresos", "costos", "gastos"):
        _filtrar_nivel(estado[clave], nivel)
    return render(request, "contabilidad/estado_resultados.html", {
        "e": estado, "desde": desde, "hasta": hasta, "nivel": nivel, "seccion": "resultados", "fechas": _fechas(desde, hasta),
        "borradores": Asiento.objects.filter(empresa=request.empresa, estado=Asiento.Estado.BORRADOR).count()})


@con_empresa("contabilidad.view_asiento")
def estado_situacion(request):
    hasta = parse_date(request.GET.get("hasta") or "") or timezone.localdate()
    nivel = _nivel(request)
    estado = servicios.estado_situacion(request.empresa, hasta)
    for clave in ("activo", "pasivo", "patrimonio"):
        _filtrar_nivel(estado[clave], nivel)
    return render(request, "contabilidad/estado_situacion.html", {
        "e": estado, "hasta": hasta, "nivel": nivel, "seccion": "situacion", "fechas": _fechas(None, hasta),
        "borradores": Asiento.objects.filter(empresa=request.empresa, estado=Asiento.Estado.BORRADOR).count()})


# --------------------------------------------------------------------------- activos fijos
@con_empresa("contabilidad.view_activofijo")
def activos(request):
    qs = ActivoFijo.objects.filter(empresa=request.empresa).select_related("cuenta_contable").order_by("codigo")
    estado, texto = request.GET.get("estado", ""), request.GET.get("q", "").strip()
    if estado in ActivoFijo.Estado.values:
        qs = qs.filter(estado=estado)
    if texto:
        qs = qs.filter(Q(descripcion__icontains=texto) | Q(codigo__icontains=texto) | Q(custodio__icontains=texto)
                       | Q(ubicacion__icontains=texto))
    lista = list(qs)
    vigentes = [a for a in lista if a.estado != ActivoFijo.Estado.BAJA]
    return render(request, "contabilidad/activos.html", {
        "activos": lista, "estado": estado, "q": texto, "estados": ActivoFijo.Estado.choices, "seccion": "activos",
        "total_costo": sum((a.costo for a in vigentes), 0),
        "total_libros": sum((a.valor_libros() for a in vigentes if a.valor_libros() is not None), 0),
        "sin_depreciar": sum(1 for a in vigentes if a.valor_libros() is None)})


def _form_activo(request, activo=None):
    form = ActivoFijoForm(request.POST or None, instance=activo, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        nuevo = form.save(commit=False)
        nuevo.empresa = request.empresa
        try:
            servicios.guardar_con_codigo(nuevo, "AF")
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, f"Activo {nuevo} guardado.")
            return redirect("contabilidad:activos")
    return render(request, "contabilidad/formulario.html", {
        "form": form, "seccion": "activos", "titulo": "Editar activo fijo" if activo else "Nuevo activo fijo",
        "volver": reverse("contabilidad:activos"),
        "secciones": [
            ("Activo", "box", "Muebles, equipos o vehículos de la empresa.",
             ["descripcion", "codigo", "cuenta_contable", "estado", "cantidad", "valor_unitario"]),
            ("Compra", "receipt", "Con la fecha de compra y la vida útil se calcula la depreciación (línea recta).",
             ["fecha_compra", "vida_util_anios", "proveedor", "factura"]),
            ("Custodia", "person-badge", "Dónde está y quién responde por él.", ["ubicacion", "custodio", "observaciones"]),
        ]})


@con_empresa("contabilidad.add_activofijo")
def activo_crear(request):
    return _form_activo(request)


@con_empresa("contabilidad.change_activofijo")
def activo_editar(request, pk):
    return _form_activo(request, get_object_or_404(ActivoFijo, pk=pk, empresa=request.empresa))
