"""
Mensajería interna tipo Messenger (solo TI, administradores y supervisores).

La misma «ventana de chat» (_ventana.html) se usa en la página Mensajes y en las ventanitas flotantes que se
abren desde cualquier pantalla (abajo a la derecha). Todo lo de una conversación lleva su número en el id
(hilo-5, sondeo-5…), así pueden convivir varias abiertas a la vez.
"""
import json
import time
from datetime import datetime, timezone as tz

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import validadores

from . import servicios
from .acceso import requiere_mensajeria
from .models import Conversacion, Mensaje

EMOJIS = "😀 😂 😊 😍 😉 😎 🤔 😅 🙂 😮 😢 😡 😴 🤝 👍 👎 👏 🙏 🙌 💪 👀 🔥 ✅ ❌ ⚠️ ❤️ 🎉 💯 ⏰ 📎 📌 📞 💻 🖨️ ✨".split()


def _conversacion(request, pk) -> Conversacion:
    try:
        return servicios.conversacion_de(request.user, pk)
    except Conversacion.DoesNotExist as error:
        raise Http404("La conversación no existe o usted no participa en ella.") from error


def _es_htmx(request) -> bool:
    return bool(request.headers.get("HX-Request"))


def _abrir(request, conversacion):
    """En la ventanita flotante: avisa al navegador que abra el chat. En la página: va al chat."""
    if _es_htmx(request):
        respuesta = HttpResponse(status=204)
        respuesta["HX-Trigger"] = json.dumps({"abrirChat": {"pk": conversacion.pk}})
        return respuesta
    return redirect("mensajes:conversacion", pk=conversacion.pk)


# --------------------------------------------------------------------------- contexto
def _panel(request, actual_id=None) -> dict:
    """Lista de chats y personas (con su punto verde de en línea)."""
    conversaciones = list(servicios.conversaciones_de(request.user))
    personas = servicios.anotar_presencia(servicios.contactos(request.user))
    por_id = {p.pk: p for p in personas}
    for c in conversaciones:
        c.titulo = servicios.titulo_para(c, request.user)
        c.otro = None if c.es_grupo else servicios.otro_de(c, request.user)
        if c.otro is not None and c.otro.pk in por_id:
            c.otro = por_id[c.otro.pk]
    personas.sort(key=lambda p: (not p.en_linea, (p.get_full_name() or p.username).lower()))
    return {"conversaciones": conversaciones, "personas": personas, "actual_id": actual_id}


def _estado(request, conversacion) -> dict:
    """Lo que cambia mientras el chat está abierto: escribiendo, visto y la conexión del otro."""
    otro = None if conversacion.es_grupo else servicios.otro_de(conversacion, request.user)
    if otro is not None:
        servicios.anotar_presencia([otro])
    return {"escriben": servicios.escribiendo(conversacion, request.user),
            "visto": servicios.visto(conversacion, request.user), "otro": otro}


def _marca(margen: float = 0) -> str:
    """Momento de la última consulta: el sondeo trae lo editado o eliminado después (con margen, por si acaso)."""
    return f"{time.time() - margen:.3f}"


def _ventana(request, conversacion, modo) -> dict:
    servicios.marcar_leida(request.user, conversacion)
    lista = servicios.mensajes(conversacion)
    return {
        "marca": _marca(),
        "actual": conversacion, "titulo": servicios.titulo_para(conversacion, request.user), "modo": modo,
        "miembros": [p.usuario for p in conversacion.participantes.all() if p.activo],
        "mensajes": lista, "ultimo_id": lista[-1].pk if lista else 0,
        "contactos": servicios.contactos(request.user), "emojis": EMOJIS,
    } | _estado(request, conversacion)


# --------------------------------------------------------------------------- páginas
@requiere_mensajeria
def bandeja(request):
    return render(request, "mensajes/bandeja.html", _panel(request))


@requiere_mensajeria
def conversacion(request, pk):
    actual = _conversacion(request, pk)
    contexto = _ventana(request, actual, "pagina")
    return render(request, "mensajes/bandeja.html", _panel(request, actual.pk) | contexto)


# --------------------------------------------------------------------------- piezas (HTMX)
@requiere_mensajeria
def panel(request):
    """Los chats y las personas (popup flotante y la columna de la página; se refresca cada 10 s)."""
    modo = "dock" if request.GET.get("modo") == "dock" else "pagina"
    plantilla = "mensajes/_panel_contenido.html" if request.GET.get("solo") == "contenido" else "mensajes/_panel.html"
    return render(request, plantilla, _panel(request, request.GET.get("actual")) | {"modo": modo})


@requiere_mensajeria
def ventana(request, pk):
    """La ventanita flotante de un chat."""
    return render(request, "mensajes/_ventana.html", _ventana(request, _conversacion(request, pk), "flotante"))


@requiere_mensajeria
def nuevos(request, pk):
    """Cada 3 s por chat abierto: mensajes nuevos, quién escribe, «Visto» y la conexión del otro."""
    actual = _conversacion(request, pk)
    try:
        despues = int(request.GET.get("despues", 0))
    except ValueError:
        despues = 0
    marca = _marca()
    lista = servicios.mensajes(actual, despues_de=despues)
    if lista and request.GET.get("leer") != "0":  # una ventanita minimizada no marca como leído
        servicios.marcar_leida(request.user, actual)
    try:
        desde = datetime.fromtimestamp(float(request.GET.get("cambios", "")), tz=tz.utc)
    except (TypeError, ValueError, OverflowError):
        desde = None
    cambiados = servicios.cambiados(actual, desde, despues) if desde and despues else []
    return render(request, "mensajes/_nuevos.html", {
        "mensajes": lista, "cambiados": cambiados, "actual": actual, "ultimo_id": lista[-1].pk if lista else despues,
        "marca": marca,
    } | _estado(request, actual))


@requiere_mensajeria
@require_POST
def escribiendo(request, pk):
    servicios.marcar_escribiendo(request.user, _conversacion(request, pk))
    return HttpResponse(status=204)


@requiere_mensajeria
@require_POST
def enviar(request, pk):
    actual = _conversacion(request, pk)
    try:
        mensaje = servicios.enviar(request.user, actual, request.POST.get("texto", ""), request.FILES.get("adjunto"))
    except ValidationError as error:
        if _es_htmx(request):
            return render(request, "mensajes/_error_envio.html", {"error": " ".join(error.messages), "actual": actual})
        messages.error(request, " ".join(error.messages))
        return redirect("mensajes:conversacion", pk=actual.pk)
    if _es_htmx(request):
        return render(request, "mensajes/_nuevos.html", {"mensajes": [mensaje], "actual": actual, "ultimo_id": mensaje.pk,
                                                         "marca": _marca(margen=10)} | _estado(request, actual))
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
    if _es_htmx(request):
        return render(request, "mensajes/_mensaje.html", {"m": mensaje, "actual": mensaje.conversacion})
    return redirect("mensajes:conversacion", pk=mensaje.conversacion_id)


@requiere_mensajeria
@require_POST
def editar(request, pk):
    """Corrige el texto de un mensaje propio (queda «Editado»; la base guarda la versión anterior)."""
    mensaje = get_object_or_404(Mensaje.objects.select_related("conversacion", "autor"), pk=pk)
    _conversacion(request, mensaje.conversacion_id)
    error = ""
    try:
        servicios.editar(request.user, mensaje, request.POST.get("texto", ""))
    except ValidationError as e:
        error = " ".join(e.messages)
    if _es_htmx(request):
        return render(request, "mensajes/_mensaje.html", {"m": mensaje, "actual": mensaje.conversacion, "error_edicion": error})
    if error:
        messages.error(request, error)
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


# --------------------------------------------------------------------------- crear y organizar
@requiere_mensajeria
@require_POST
def nueva_directa(request):
    otro = servicios.contactos(request.user).filter(pk=validadores.entero(request.POST.get("usuario"))).first()
    if otro is None:
        messages.error(request, "Esa persona no está en la mensajería interna.")
        return redirect("mensajes:bandeja")
    return _abrir(request, servicios.conversacion_directa(request.user, otro))


@requiere_mensajeria
@require_POST
def nuevo_grupo(request):
    miembros = list(servicios.contactos(request.user).filter(pk__in=validadores.enteros(request.POST.getlist("miembros"))))
    try:
        grupo = servicios.crear_grupo(request.user, request.POST.get("nombre", ""), miembros)
    except ValidationError as error:
        if _es_htmx(request):
            return HttpResponse(f'<div class="alert alert-danger small py-2 mb-2">{" ".join(error.messages)}</div>')
        messages.error(request, " ".join(error.messages))
        return redirect("mensajes:bandeja")
    return _abrir(request, grupo)


@requiere_mensajeria
@require_POST
def agregar(request, pk):
    grupo = _conversacion(request, pk)
    miembros = list(servicios.contactos(request.user).filter(pk__in=validadores.enteros(request.POST.getlist("miembros"))))
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
    return redirect("mensajes:bandeja")
