"""Mesa de ayuda (/soporte/): cualquier persona reporta una falla; TI ve la bandeja completa y la atiende."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import soporte
from .forms import ComentarioTicketForm, ConfirmarTicketForm, GestionTicketForm, TicketForm
from .models import Ticket
from .servicios import OperacionNoPermitida

ORDEN_PRIORIDAD = Case(*[When(prioridad=valor, then=Value(i)) for i, valor in enumerate(Ticket.Prioridad.values)],
                       output_field=IntegerField())


@login_required
def inicio(request):
    if soporte.es_ti(request.user):
        return _bandeja_ti(request)
    tickets = (Ticket.objects.filter(reportado_por=request.user).select_related("asignado_a")
               .order_by(Case(When(estado__in=Ticket.ACTIVOS + (Ticket.Estado.RESUELTO,), then=Value(0)), default=Value(1)),
                         "-creado_en"))
    pagina = Paginator(tickets, 20).get_page(request.GET.get("pagina"))
    return render(request, "soporte/mis_reportes.html", {
        "pagina": pagina, "por_confirmar": tickets.filter(estado=Ticket.Estado.RESUELTO).count(), "plazos": soporte.tabla_plazos(),
    })


def _bandeja_ti(request):
    vista = request.GET.get("ver", "activos")
    qs = Ticket.objects.select_related("reportado_por", "asignado_a", "equipo")
    if vista == "activos":
        qs = qs.filter(estado__in=Ticket.ACTIVOS)
    elif vista == "mios":
        qs = qs.filter(estado__in=Ticket.ACTIVOS, asignado_a=request.user)
    elif vista == "por_confirmar":
        qs = qs.filter(estado=Ticket.Estado.RESUELTO)
    elif vista == "vencidos":
        ahora = timezone.now()
        qs = qs.filter(estado__in=Ticket.ACTIVOS).filter(
            Q(primera_respuesta_en__isnull=True, limite_respuesta__lt=ahora) | Q(limite_solucion__lt=ahora))
    else:
        vista = "todos"
    categoria = request.GET.get("categoria", "")
    if categoria in Ticket.Categoria.values:
        qs = qs.filter(categoria=categoria)
    texto = request.GET.get("q", "").strip()
    if texto:
        filtro = (Q(titulo__icontains=texto) | Q(descripcion__icontains=texto) | Q(reportado_por__first_name__icontains=texto)
                  | Q(reportado_por__last_name__icontains=texto) | Q(equipo__codigo__icontains=texto))
        numero = texto.upper().removeprefix("SOP-").lstrip("0")
        if numero.isdigit():
            filtro |= Q(pk=int(numero))
        qs = qs.filter(filtro)
    if vista == "todos":
        qs = qs.order_by("-creado_en")
    else:  # lo más urgente arriba: prioridad y, dentro de ella, el plazo que vence primero
        qs = qs.order_by(ORDEN_PRIORIDAD, "limite_solucion")
    dias = int(request.GET["dias"]) if request.GET.get("dias") in ("7", "30", "90") else 30
    return render(request, "soporte/bandeja.html", {
        "pagina": Paginator(qs, 30).get_page(request.GET.get("pagina")), "ver": vista, "categoria": categoria, "q": texto,
        "categorias": Ticket.Categoria.choices, "resumen": soporte.resumen(), "indicadores": soporte.indicadores(dias),
        "plazos": soporte.tabla_plazos(), "dias": dias,
    })


@login_required
def crear(request):
    es_ti = soporte.es_ti(request.user)
    form = TicketForm(request.POST or None, es_ti=es_ti, initial={"prioridad": Ticket.Prioridad.MEDIA})
    if request.method == "POST" and form.is_valid():
        ticket = soporte.crear_ticket(usuario=request.user, request=request, **form.cleaned_data)
        messages.success(request, f"Reporte {ticket.numero} enviado a TI. Le avisaremos aquí cuando lo atiendan.")
        return redirect("soporte:detalle", pk=ticket.pk)
    return render(request, "soporte/nuevo.html", {"form": form, "plazos": soporte.tabla_plazos()})


def _ticket_visible(request, pk) -> Ticket:
    ticket = get_object_or_404(Ticket.objects.select_related("reportado_por", "asignado_a", "equipo"), pk=pk)
    if not soporte.puede_ver(request.user, ticket):
        raise PermissionDenied("Este reporte es de otra persona.")
    return ticket


@login_required
def detalle(request, pk):
    ticket = _ticket_visible(request, pk)
    es_ti = soporte.es_ti(request.user)
    form = ComentarioTicketForm(request.POST or None, es_ti=es_ti)
    if request.method == "POST" and form.is_valid():
        try:
            soporte.comentar(ticket, request.user, form.cleaned_data["texto"], interno=form.cleaned_data.get("interno", False))
        except OperacionNoPermitida as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Mensaje enviado.")
        return redirect("soporte:detalle", pk=ticket.pk)
    comentarios = ticket.comentarios.select_related("autor")
    if not es_ti:
        comentarios = comentarios.filter(interno=False)
    gestion = None
    if es_ti:
        gestion = GestionTicketForm(initial={"estado": ticket.estado, "prioridad": ticket.prioridad, "asignado_a": ticket.asignado_a,
                                             "equipo": ticket.equipo, "solucion": ticket.solucion})
    return render(request, "soporte/detalle.html", {
        "ticket": ticket, "comentarios": comentarios, "form": form, "gestion": gestion, "es_ti": es_ti,
        "puede_confirmar": ticket.estado == Ticket.Estado.RESUELTO and ticket.reportado_por_id == request.user.pk,
        "puede_escribir": es_ti or ticket.estado != Ticket.Estado.CERRADO,
        "otros_del_equipo": (Ticket.objects.filter(equipo=ticket.equipo).exclude(pk=ticket.pk).order_by("-creado_en")[:5]
                             if es_ti and ticket.equipo_id else []),
    })


@login_required
@require_POST
def gestionar(request, pk):
    if not soporte.es_ti(request.user):
        raise PermissionDenied("Solo TI gestiona los reportes.")
    ticket = get_object_or_404(Ticket, pk=pk)
    form = GestionTicketForm(request.POST)
    if not form.is_valid():
        for errores in form.errors.values():
            for error in errores:
                messages.error(request, error)
        return redirect("soporte:detalle", pk=pk)
    try:
        soporte.gestionar(ticket, request.user, **form.cleaned_data)
    except OperacionNoPermitida as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Reporte {ticket.numero} actualizado.")
    return redirect("soporte:detalle", pk=pk)


@login_required
@require_POST
def confirmar(request, pk):
    ticket = _ticket_visible(request, pk)
    form = ConfirmarTicketForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Elija si quedó solucionado o no.")
        return redirect("soporte:detalle", pk=pk)
    try:
        soporte.confirmar(ticket, request.user, **form.cleaned_data)
    except OperacionNoPermitida as error:
        messages.error(request, str(error))
    else:
        if form.cleaned_data["solucionado"]:
            messages.success(request, "Gracias. El reporte quedó cerrado.")
        else:
            messages.warning(request, "Se reabrió el reporte y se avisó a TI.")
    return redirect("soporte:detalle", pk=pk)
