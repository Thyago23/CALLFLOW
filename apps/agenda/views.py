from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.models import Notificacion

from . import servicios
from .forms import EventoForm
from .models import Evento

VISTAS = [("mes", "Mes"), ("semana", "Semana"), ("dia", "Día")]


def _fecha(request) -> date:
    return parse_date(request.GET.get("fecha") or "") or timezone.localdate()


def _sumar_meses(dia: date, meses: int) -> date:
    mes = dia.month - 1 + meses
    return date(dia.year + mes // 12, mes % 12 + 1, 1)


def _url(vista: str, fecha: date) -> str:
    return f"{reverse('agenda:calendario')}?vista={vista}&fecha={fecha.isoformat()}"


@login_required
@permission_required("agenda.view_evento", raise_exception=True)
def calendario(request):
    usuario = request.user
    vista = request.GET.get("vista", "mes")
    if vista not in dict(VISTAS):
        vista = "mes"
    fecha = _fecha(request)

    if vista == "mes":
        anterior, siguiente = _sumar_meses(fecha, -1), _sumar_meses(fecha, 1)
        contexto = {"semanas": servicios.semanas_del_mes(usuario, fecha)}
    elif vista == "semana":
        anterior, siguiente = fecha - timedelta(days=7), fecha + timedelta(days=7)
        dias = servicios.dias_de_la_semana(usuario, fecha)
        contexto = {"dias": dias, "lunes": dias[0].fecha, "domingo": dias[-1].fecha}
    else:
        anterior, siguiente = fecha - timedelta(days=1), fecha + timedelta(days=1)
        contexto = {"dia": servicios.eventos_del_dia(usuario, fecha)}

    contexto.update({
        "vista": vista, "vistas": VISTAS, "fecha": fecha,
        "url_anterior": _url(vista, anterior), "url_siguiente": _url(vista, siguiente),
        "url_hoy": _url(vista, timezone.localdate()),
        "vencidos": servicios.vencidos(usuario)[:8],
        "proximos": servicios.proximos(usuario)[:8],
    })
    return render(request, "agenda/calendario.html", contexto)


def _evento_visible(request, pk) -> Evento:
    return get_object_or_404(servicios.eventos_de(request.user), pk=pk)


def _evento_propio(request, pk) -> Evento:
    evento = _evento_visible(request, pk)
    if not servicios.puede_editar(request.user, evento):
        raise PermissionDenied("Solo quien creó el evento puede modificarlo.")
    return evento


@login_required
@permission_required("agenda.add_evento", raise_exception=True)
def crear(request):
    inicial = {"fecha": _fecha(request)}
    if request.GET.get("tipo") in Evento.Tipo.values:
        inicial["tipo"] = request.GET["tipo"]
    else:
        inicial["tipo"] = Evento.Tipo.ACTIVIDAD
    form = EventoForm(request.POST or None, usuario=request.user, initial=inicial)
    if request.method == "POST" and form.is_valid():
        evento = form.aplicar(Evento(propietario=request.user))
        servicios.guardar_evento(evento, form.cleaned_data["participantes"], avisar_de_nuevo=True)
        messages.success(request, f"«{evento.titulo}» agregado a la agenda.")
        return redirect(_url("dia", timezone.localtime(evento.inicio).date()))
    return render(request, "agenda/evento_form.html", {"form": form, "nuevo": True})


@login_required
@permission_required("agenda.view_evento", raise_exception=True)
def detalle(request, pk):
    evento = _evento_visible(request, pk)
    return render(request, "agenda/evento_detalle.html", {
        "evento": evento,
        "participantes": evento.participantes.select_related("usuario").order_by("usuario__first_name"),
        "puede_editar": servicios.puede_editar(request.user, evento),
        "puede_eliminar": evento.propietario_id == request.user.pk and request.user.has_perm("agenda.delete_evento"),
        "aviso": servicios.momento_aviso(evento),
        "aviso_entregado": Notificacion.objects.filter(
            tipo=Notificacion.Tipo.AGENDA, url=reverse("agenda:detalle", args=[evento.pk])).exists(),
    })


@login_required
@permission_required("agenda.change_evento", raise_exception=True)
def editar(request, pk):
    evento = _evento_propio(request, pk)
    if request.method == "POST":
        form = EventoForm(request.POST, usuario=request.user)
        if form.is_valid():
            antes = (evento.inicio, evento.todo_el_dia, evento.minutos_aviso)
            form.aplicar(evento)
            cambio_horario = antes != (evento.inicio, evento.todo_el_dia, evento.minutos_aviso)
            servicios.guardar_evento(evento, form.cleaned_data["participantes"], avisar_de_nuevo=cambio_horario)
            messages.success(request, "Evento actualizado.")
            return redirect("agenda:detalle", pk=evento.pk)
    else:
        form = EventoForm.desde_evento(evento, usuario=request.user)
    return render(request, "agenda/evento_form.html", {"form": form, "nuevo": False, "evento": evento})


@login_required
@permission_required("agenda.change_evento", raise_exception=True)
@require_POST
def cambiar_estado(request, pk):
    evento = _evento_propio(request, pk)
    estado = request.POST.get("estado")
    if estado not in Evento.Estado.values:
        messages.error(request, "Estado no válido.")
    else:
        servicios.cambiar_estado(evento, estado)
        textos = {"COMPLETADO": "marcado como completado", "CANCELADO": "cancelado", "PENDIENTE": "reabierto"}
        messages.success(request, f"«{evento.titulo}» {textos[estado]}.")
    volver = request.POST.get("volver")
    if not volver or not url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}):
        volver = reverse("agenda:detalle", args=[evento.pk])
    return redirect(volver)


@login_required
@permission_required("agenda.delete_evento", raise_exception=True)
@require_POST
def eliminar(request, pk):
    evento = _evento_visible(request, pk)
    if evento.propietario_id != request.user.pk:
        raise PermissionDenied("Solo quien creó el evento puede eliminarlo.")
    fecha = timezone.localtime(evento.inicio).date()
    titulo = evento.titulo
    evento.delete()
    messages.success(request, f"«{titulo}» eliminado.")
    return redirect(_url("dia", fecha))
