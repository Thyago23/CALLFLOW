"""CRM de visas y paquetes: leads (con su seguimiento y conversión a cliente), clientes y proveedores."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import servicios
from .empresa import con_empresa
from .forms import ClienteForm, LeadForm, ProveedorForm, SeguimientoForm
from .models import Cliente, Lead, Linea, MovimientoFinanciero, Proveedor, TipoIdentificacion
from .views import _errores


# --------------------------------------------------------------------------- leads
@con_empresa("contabilidad.view_lead")
def leads(request):
    qs = Lead.objects.filter(empresa=request.empresa).select_related("responsable", "cliente")
    estado = request.GET.get("estado", "abiertos")
    interes, origen, texto = request.GET.get("interes", ""), request.GET.get("origen", ""), request.GET.get("q", "").strip()
    if estado == "abiertos":
        qs = qs.filter(estado__in=Lead.ABIERTOS)
    elif estado == "vencidos":
        qs = qs.filter(estado__in=Lead.ABIERTOS, proximo_contacto__lt=timezone.localdate())
    elif estado in Lead.Estado.values:
        qs = qs.filter(estado=estado)
    if interes in Lead.Interes.values:
        qs = qs.filter(interes=interes)
    if origen in Lead.Origen.values:
        qs = qs.filter(origen=origen)
    if request.GET.get("mios") == "1":
        qs = qs.filter(responsable=request.user)
    if texto:
        qs = qs.filter(Q(nombres__icontains=texto) | Q(telefono__icontains=texto) | Q(correo__icontains=texto)
                       | Q(detalle__icontains=texto))
    # Primero los que hay que llamar (próximo contacto más cercano), luego los más nuevos.
    qs = qs.order_by("proximo_contacto", "-creado_en") if estado in ("abiertos", "vencidos") else qs.order_by("-actualizado_en")
    base = Lead.objects.filter(empresa=request.empresa)
    return render(request, "contabilidad/leads.html", {
        "leads": qs[:500], "estado": estado, "interes": interes, "origen": origen, "q": texto, "mios": request.GET.get("mios"),
        "estados": Lead.Estado.choices, "intereses": Lead.Interes.choices, "origenes": Lead.Origen.choices,
        "hoy": timezone.localdate(), "seccion": "leads",
        "conteo": {e: base.filter(estado=e).count() for e in Lead.Estado.values},
        "vencidos": base.filter(estado__in=Lead.ABIERTOS, proximo_contacto__lt=timezone.localdate()).count()})


def _form_lead(request, lead=None):
    form = LeadForm(request.POST or None, instance=lead,
                    initial=None if lead else {"responsable": request.user.pk if request.user.has_perm("contabilidad.change_lead") else None})
    if request.method == "POST" and form.is_valid():
        guardado = form.save(commit=False)
        if not lead:
            guardado.empresa, guardado.registrado_por = request.empresa, request.user
        try:
            servicios._guardar(guardado)
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, f"Lead «{guardado.nombres}» guardado.")
            return redirect("crm:lead_detalle", pk=guardado.pk)
    return render(request, "contabilidad/formulario.html", {
        "form": form, "seccion": "leads", "titulo": "Editar lead" if lead else "Nuevo lead",
        "volver": reverse("crm:lead_detalle", args=[lead.pk]) if lead else reverse("crm:leads"),
        "secciones": [
            ("Persona", "person", "Con un teléfono o un correo basta para empezar.", ["nombres", "telefono", "correo"]),
            ("Qué busca", "airplane", "Visa o paquete turístico, y de dónde llegó el contacto.", ["interes", "detalle", "origen"]),
            ("Seguimiento", "calendar-check", "Quién lo atiende y cuándo volver a contactarlo.",
             ["responsable", "proximo_contacto", "notas"]),
        ]})


@con_empresa("contabilidad.add_lead")
def lead_crear(request):
    return _form_lead(request)


@con_empresa("contabilidad.change_lead")
def lead_editar(request, pk):
    lead = get_object_or_404(Lead, pk=pk, empresa=request.empresa)
    if not lead.abierto:
        messages.info(request, "El lead ya está cerrado: no se edita.")
        return redirect("crm:lead_detalle", pk=pk)
    return _form_lead(request, lead)


@con_empresa("contabilidad.view_lead")
def lead_detalle(request, pk):
    lead = get_object_or_404(Lead.objects.select_related("responsable", "cliente", "registrado_por"), pk=pk,
                             empresa=request.empresa)
    form = SeguimientoForm(initial={"estado": Lead.Estado.CONTACTADO if lead.estado == Lead.Estado.NUEVO else lead.estado})
    return render(request, "contabilidad/lead_detalle.html", {
        "lead": lead, "seguimientos": lead.seguimientos.select_related("usuario"), "form": form, "seccion": "leads"})


@con_empresa("contabilidad.change_lead")
@require_POST
def lead_seguimiento(request, pk):
    lead = get_object_or_404(Lead, pk=pk, empresa=request.empresa)
    form = SeguimientoForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            servicios.registrar_seguimiento(lead, request.user, d["nota"], d["estado"], d["proximo_contacto"],
                                            d["motivo_perdida"])
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, "Seguimiento registrado.")
            return redirect("crm:lead_detalle", pk=pk)
    return render(request, "contabilidad/lead_detalle.html", {
        "lead": lead, "seguimientos": lead.seguimientos.select_related("usuario"), "form": form, "seccion": "leads"})


@con_empresa("contabilidad.change_lead", "contabilidad.add_cliente")
def lead_convertir(request, pk):
    """El lead contrató: se completan los datos que faltan (identificación) y queda como cliente."""
    lead = get_object_or_404(Lead, pk=pk, empresa=request.empresa)
    if not lead.abierto:
        messages.info(request, "Este lead ya está cerrado.")
        return redirect("crm:lead_detalle", pk=pk)
    linea = Linea.PAQUETES if lead.interes == Lead.Interes.PAQUETE else Linea.VISAS
    form = ClienteForm(request.POST or None, empresa=request.empresa, initial={
        "nombres": lead.nombres, "telefono": lead.telefono, "correo": lead.correo, "linea": linea,
        "tipo_identificacion": TipoIdentificacion.CEDULA,
        "observaciones": "\n".join(t for t in [lead.detalle, lead.notas] if t)})
    if request.method == "POST" and form.is_valid():
        try:
            cliente = servicios.convertir_lead(lead, form.save(commit=False), request.user)
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, f"{lead.nombres} ya es cliente ({cliente.codigo}).")
            return redirect("crm:cliente_detalle", pk=cliente.pk)
    return render(request, "contabilidad/formulario.html", {
        "form": form, "seccion": "leads", "titulo": f"Convertir en cliente: {lead.nombres}", "boton": "Crear cliente",
        "volver": reverse("crm:lead_detalle", args=[pk]),
        "secciones": [
            ("Cliente", "person-check", "Se completan los datos del lead. La identificación es obligatoria para el cliente.",
             ["linea", "nombres", "tipo_identificacion", "identificacion"]),
            ("Contacto", "telephone", "", ["telefono", "correo", "direccion", "observaciones"]),
        ]})


# --------------------------------------------------------------------------- clientes
@con_empresa("contabilidad.view_cliente")
def clientes(request):
    qs = Cliente.objects.filter(empresa=request.empresa).order_by("-activo", "nombres")
    linea, texto, estado = request.GET.get("linea", ""), request.GET.get("q", "").strip(), request.GET.get("estado", "")
    if linea in Linea.values:
        qs = qs.filter(linea__in=[linea, Linea.AMBAS])
    if estado == "inactivos":
        qs = qs.filter(activo=False)
    elif estado != "todos":
        qs = qs.filter(activo=True)
    if texto:
        qs = qs.filter(Q(nombres__icontains=texto) | Q(identificacion__icontains=texto) | Q(codigo__icontains=texto)
                       | Q(telefono__icontains=texto))
    return render(request, "contabilidad/clientes.html", {
        "clientes": qs[:500], "linea": linea, "q": texto, "estado": estado, "lineas": Linea.choices, "seccion": "clientes"})


def _form_cliente(request, cliente=None):
    form = ClienteForm(request.POST or None, instance=cliente, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        guardado = form.save(commit=False)
        if not cliente:
            guardado.empresa, guardado.registrado_por = request.empresa, request.user
        try:
            servicios.guardar_con_codigo(guardado, "CLI")
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, f"Cliente {guardado} guardado.")
            return redirect("crm:cliente_detalle", pk=guardado.pk)
    return render(request, "contabilidad/formulario.html", {
        "form": form, "seccion": "clientes", "titulo": "Editar cliente" if cliente else "Nuevo cliente",
        "volver": reverse("crm:cliente_detalle", args=[cliente.pk]) if cliente else reverse("crm:clientes"),
        "secciones": [
            ("Cliente", "person", "Cliente de visas o de paquetes (no es un cliente del call).",
             ["linea", "nombres", "tipo_identificacion", "identificacion"] + (["activo"] if cliente else [])),
            ("Contacto", "telephone", "Datos personales: solo los ve quien lleva la contabilidad.",
             ["telefono", "correo", "direccion", "observaciones"]),
        ]})


@con_empresa("contabilidad.add_cliente")
def cliente_crear(request):
    return _form_cliente(request)


@con_empresa("contabilidad.change_cliente")
def cliente_editar(request, pk):
    return _form_cliente(request, get_object_or_404(Cliente, pk=pk, empresa=request.empresa))


def _resumen_tercero(movimientos):
    vigentes = movimientos.filter(anulado=False)
    return {
        "entradas": vigentes.filter(tipo__in=MovimientoFinanciero.ENTRADAS).aggregate(t=Sum("valor"))["t"] or 0,
        "salidas": vigentes.filter(tipo__in=MovimientoFinanciero.SALIDAS).aggregate(t=Sum("valor"))["t"] or 0,
    }


@con_empresa("contabilidad.view_cliente")
def cliente_detalle(request, pk):
    cliente = get_object_or_404(Cliente.objects.select_related("registrado_por"), pk=pk, empresa=request.empresa)
    movimientos = cliente.movimientos.select_related("cuenta").order_by("-fecha", "-pk")
    return render(request, "contabilidad/tercero_detalle.html", {
        "tercero": cliente, "es_cliente": True, "movimientos": movimientos[:200], "totales": _resumen_tercero(movimientos),
        "lead": getattr(cliente, "lead", None), "seccion": "clientes"})


# --------------------------------------------------------------------------- proveedores
@con_empresa("contabilidad.view_proveedor")
def proveedores(request):
    qs = Proveedor.objects.filter(empresa=request.empresa).order_by("-activo", "nombre")
    tipo, texto = request.GET.get("tipo", ""), request.GET.get("q", "").strip()
    if tipo in Proveedor.Tipo.values:
        qs = qs.filter(tipo=tipo)
    if texto:
        qs = qs.filter(Q(nombre__icontains=texto) | Q(identificacion__icontains=texto) | Q(codigo__icontains=texto)
                       | Q(contacto__icontains=texto))
    return render(request, "contabilidad/proveedores.html", {
        "proveedores": qs[:500], "tipo": tipo, "q": texto, "tipos": Proveedor.Tipo.choices, "seccion": "proveedores"})


def _form_proveedor(request, proveedor=None):
    form = ProveedorForm(request.POST or None, instance=proveedor, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        guardado = form.save(commit=False)
        if not proveedor:
            guardado.empresa = request.empresa
        try:
            servicios.guardar_con_codigo(guardado, "PRV")
        except ValidationError as error:
            _errores(form, error)
        else:
            messages.success(request, f"Proveedor {guardado} guardado.")
            return redirect("crm:proveedor_detalle", pk=guardado.pk)
    return render(request, "contabilidad/formulario.html", {
        "form": form, "seccion": "proveedores", "titulo": "Editar proveedor" if proveedor else "Nuevo proveedor",
        "volver": reverse("crm:proveedor_detalle", args=[proveedor.pk]) if proveedor else reverse("crm:proveedores"),
        "secciones": [
            ("Proveedor", "truck", "Aerolíneas, hoteles, operadores, consulados y proveedores locales.",
             ["nombre", "tipo", "tipo_identificacion", "identificacion"] + (["activo"] if proveedor else [])),
            ("Contacto y pago", "telephone", "", ["contacto", "telefono", "correo", "condicion_pago", "dias_credito",
                                                  "observaciones"]),
        ]})


@con_empresa("contabilidad.add_proveedor")
def proveedor_crear(request):
    return _form_proveedor(request)


@con_empresa("contabilidad.change_proveedor")
def proveedor_editar(request, pk):
    return _form_proveedor(request, get_object_or_404(Proveedor, pk=pk, empresa=request.empresa))


@con_empresa("contabilidad.view_proveedor")
def proveedor_detalle(request, pk):
    proveedor = get_object_or_404(Proveedor, pk=pk, empresa=request.empresa)
    movimientos = proveedor.movimientos.select_related("cuenta").order_by("-fecha", "-pk")
    return render(request, "contabilidad/tercero_detalle.html", {
        "tercero": proveedor, "es_cliente": False, "movimientos": movimientos[:200],
        "totales": _resumen_tercero(movimientos), "seccion": "proveedores"})
