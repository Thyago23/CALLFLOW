"""Mensajería interna: bandeja, conversación, envío (HTMX) y grupos. Solo TI, administradores y supervisores."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import servicios
from .acceso import requiere_mensajeria
from .models import Conversacion, Mensaje


def _conversacion(request, pk) -> Conversacion:
    try:
        return servicios.conversacion_de(request.user, pk)
    except Conversacion.DoesNotExist as error:
        raise Http404("La conversación no existe o usted no participa en ella.") from error


def _bandeja(request, actual=None, error=""):
    conversaciones = list(servicios.conversaciones_de(request.user))
    for c in conversaciones:
        c.titulo = servicios.titulo_para(c, request.user)
        c.otro = None if c.es_grupo else servicios.otro_de(c, request.user)
    contexto = {"conversaciones": conversaciones, "contactos": servicios.contactos(request.user), "error": error}
    if actual is not None:
        servicios.marcar_leida(request.user, actual)
        for c in conversaciones:
            if c.pk == actual.pk:
                c.no_leidos = 0
        lista = servicios.mensajes(actual)
        contexto |= {
            "actual": actual, "titulo": servicios.titulo_para(actual, request.user),
            "otro": None if actual.es_grupo else servicios.otro_de(actual, request.user),
            "miembros": [p.usuario for p in actual.participantes.all() if p.activo],
            "mensajes": lista, "ultimo_id": lista[-1].pk if lista else 0,
        }
    return render(request, "mensajes/bandeja.html", contexto)


@requiere_mensajeria
def bandeja(request):
    return _bandeja(request)


@requiere_mensajeria
def conversacion(request, pk):
    return _bandeja(request, _conversacion(request, pk))


@requiere_mensajeria
def nuevos(request, pk):
    """HTMX (cada pocos segundos): los mensajes que llegaron después del último que se ve."""
    actual = _conversacion(request, pk)
    try:
        despues = int(request.GET.get("despues", 0))
    except ValueError:
        despues = 0
    lista = servicios.mensajes(actual, despues_de=despues)
    if not lista:
        return HttpResponse(status=204)
    servicios.marcar_leida(request.user, actual)
    return render(request, "mensajes/_mensajes.html", {"mensajes": lista, "actual": actual, "ultimo_id": lista[-1].pk, "oob": True})


@requiere_mensajeria
def lista(request):
    """HTMX: la lista de conversaciones (para ver nuevas y cuántos no leídos hay)."""
    conversaciones = list(servicios.conversaciones_de(request.user))
    for c in conversaciones:
        c.titulo = servicios.titulo_para(c, request.user)
        c.otro = None if c.es_grupo else servicios.otro_de(c, request.user)
    actual = request.GET.get("actual")
    return render(request, "mensajes/_conversaciones.html", {"conversaciones": conversaciones, "actual_id": actual})


@requiere_mensajeria
@require_POST
def enviar(request, pk):
    actual = _conversacion(request, pk)
    try:
        mensaje = servicios.enviar(request.user, actual, request.POST.get("texto", ""), request.FILES.get("adjunto"))
    except ValidationError as error:
        if request.headers.get("HX-Request"):
            return render(request, "mensajes/_error_envio.html", {"error": " ".join(error.messages)})
        messages.error(request, " ".join(error.messages))
        return redirect("mensajes:conversacion", pk=actual.pk)
    if request.headers.get("HX-Request"):
        return render(request, "mensajes/_mensajes.html", {"mensajes": [mensaje], "actual": actual, "ultimo_id": mensaje.pk, "oob": True})
    return redirect("mensajes:conversacion", pk=actual.pk)


@requiere_mensajeria
@require_POST
def eliminar(request, pk):
    mensaje = get_object_or_404(Mensaje.objects.select_related("conversacion"), pk=pk)
    _conversacion(request, mensaje.conversacion_id)
    try:
        servicios.eliminar(request.user, mensaje)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    if request.headers.get("HX-Request"):
        return render(request, "mensajes/_mensaje.html", {"m": mensaje, "actual": mensaje.conversacion})
    return redirect("mensajes:conversacion", pk=mensaje.conversacion_id)


@requiere_mensajeria
def adjunto(request, pk):
    """El archivo solo se entrega a quien participa en la conversación."""
    mensaje = get_object_or_404(Mensaje, pk=pk, eliminado=False)
    _conversacion(request, mensaje.conversacion_id)
    if not mensaje.adjunto:
        raise Http404
    try:
        archivo = mensaje.adjunto.open("rb")
    except FileNotFoundError as error:
        raise Http404("El archivo ya no está disponible.") from error
    respuesta = FileResponse(archivo, content_type=mensaje.adjunto_tipo, filename=mensaje.adjunto_nombre,
                             as_attachment=request.GET.get("descargar") == "1")
    respuesta["X-Content-Type-Options"] = "nosniff"
    return respuesta


@requiere_mensajeria
@require_POST
def nueva_directa(request):
    otro = servicios.contactos(request.user).filter(pk=request.POST.get("usuario")).first()
    if otro is None:
        messages.error(request, "Esa persona no está en la mensajería interna.")
        return redirect("mensajes:bandeja")
    return redirect("mensajes:conversacion", pk=servicios.conversacion_directa(request.user, otro).pk)


@requiere_mensajeria
@require_POST
def nuevo_grupo(request):
    miembros = list(servicios.contactos(request.user).filter(pk__in=request.POST.getlist("miembros")))
    try:
        grupo = servicios.crear_grupo(request.user, request.POST.get("nombre", ""), miembros)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("mensajes:bandeja")
    messages.success(request, f"Grupo «{grupo.nombre}» creado.")
    return redirect("mensajes:conversacion", pk=grupo.pk)


@requiere_mensajeria
@require_POST
def agregar(request, pk):
    grupo = _conversacion(request, pk)
    miembros = list(servicios.contactos(request.user).filter(pk__in=request.POST.getlist("miembros")))
    try:
        cantidad = servicios.agregar_miembros(request.user, grupo, miembros)
        messages.success(request, f"{cantidad} persona{'s' if cantidad != 1 else ''} agregada{'s' if cantidad != 1 else ''} al grupo.")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    return redirect("mensajes:conversacion", pk=grupo.pk)


@requiere_mensajeria
@require_POST
def salir(request, pk):
    grupo = _conversacion(request, pk)
    try:
        servicios.salir(request.user, grupo)
        messages.success(request, f"Saliste del grupo «{grupo.nombre}».")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("mensajes:conversacion", pk=grupo.pk)
    return redirect(reverse("mensajes:bandeja"))
