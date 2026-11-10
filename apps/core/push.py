"""
Notificaciones push del navegador (Web Push con claves VAPID).

Cada notificación nace con push_enviada = false (también las que crea un trigger de la base,
como el stock bajo). `enviar_pendientes` las reclama con SELECT … FOR UPDATE SKIP LOCKED, así
dos procesos nunca envían la misma, y las manda a los navegadores suscritos del destinatario.

Se dispara solo al terminar cualquier petición que haya dejado notificaciones pendientes
(PushMiddleware, en un hilo para no demorar la respuesta) y también desde el comando
`enviar_push` / `enviar_avisos_agenda` del Programador de tareas, por si nadie está usando el
sistema en ese momento.

Claves: python manage.py generar_claves_push  (las copia al .env; sin claves, no se envía nada).
"""
import json
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.contrib.staticfiles import finders
from django.db import connection, transaction
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from .models import Notificacion, SuscripcionPush

logger = logging.getLogger(__name__)

VIGENCIA = timedelta(minutes=15)  # una notificación más vieja ya no se envía como push (solo se marca)
LOTE = 200


def configurado() -> bool:
    return bool(getattr(settings, "VAPID_PUBLIC_KEY", "") and getattr(settings, "VAPID_PRIVATE_KEY", ""))


def clave_publica() -> str:
    return getattr(settings, "VAPID_PUBLIC_KEY", "") if configurado() else ""


def contenido(n: Notificacion) -> dict:
    """Lo que muestra el sistema operativo. El enlace pasa por «abrir» para marcarla como leída."""
    icono = static("img/logo-multivisa.png") if finders.find("img/logo-multivisa.png") else ""
    return {"title": n.titulo, "body": n.mensaje[:240], "url": reverse("notificacion_abrir", args=[n.pk]),
            "tag": f"callflow-{n.pk}", "icon": icono, "tipo": n.tipo}


def _reclamar() -> list[Notificacion]:
    with transaction.atomic():
        pendientes = list(Notificacion.objects.select_for_update(skip_locked=True)
                          .filter(push_enviada=False).order_by("creado_en")[:LOTE])
        if pendientes:
            Notificacion.objects.filter(pk__in=[n.pk for n in pendientes]).update(push_enviada=True)
    return pendientes


def enviar_pendientes() -> int:
    """Envía las notificaciones pendientes recientes. Devuelve cuántos mensajes push salieron."""
    if not configurado():
        return 0
    from pywebpush import WebPushException, webpush

    limite = timezone.now() - VIGENCIA
    enviados = 0
    for n in _reclamar():
        if n.creado_en < limite:
            continue
        datos = json.dumps(contenido(n))
        for s in SuscripcionPush.objects.filter(usuario_id=n.destinatario_id):
            try:
                webpush(subscription_info=s.datos, data=datos, vapid_private_key=settings.VAPID_PRIVATE_KEY,
                        vapid_claims={"sub": settings.VAPID_CONTACTO}, ttl=24 * 3600, timeout=10)
                enviados += 1
            except WebPushException as error:
                estado = getattr(getattr(error, "response", None), "status_code", None)
                if estado in (404, 410):  # el navegador anuló la suscripción
                    s.delete()
                else:
                    logger.warning("No se pudo enviar el push %s a %s: %s", n.pk, s.pk, error)
            except Exception:  # noqa: BLE001 - un navegador caído no debe frenar a los demás
                logger.exception("Error inesperado enviando el push %s", n.pk)
    return enviados


def _en_segundo_plano():
    try:
        enviar_pendientes()
    finally:
        connection.close()


class PushMiddleware:
    """Al terminar una petición, si quedaron notificaciones sin enviar, las envía en un hilo aparte."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        respuesta = self.get_response(request)
        if configurado() and Notificacion.objects.filter(push_enviada=False).exists():
            threading.Thread(target=_en_segundo_plano, name="callflow-push", daemon=True).start()
        return respuesta
