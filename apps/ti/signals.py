"""Registra desde qué equipo inicia y cierra sesión cada persona (nunca impide ingresar ni salir)."""
import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from . import servicios

logger = logging.getLogger(__name__)


@receiver(user_logged_in, dispatch_uid="ti_registrar_inicio")
def al_iniciar_sesion(sender, request, user, **kwargs):
    if request is None:
        return
    try:
        servicios.registrar_inicio(request, user)
    except Exception:
        logger.exception("No se pudo registrar el equipo de la sesión de %s", user)


@receiver(user_logged_out, dispatch_uid="ti_registrar_cierre")
def al_cerrar_sesion(sender, request, user, **kwargs):
    if request is None:
        return
    try:
        servicios.registrar_cierre(request)
    except Exception:
        logger.exception("No se pudo registrar el cierre de sesión de %s", user)
