"""
Entrada automática: el primer inicio de sesión del día registra la hora exacta de llegada.

Solo cuenta el primero de cada día (los siguientes inicios de sesión no cambian la entrada),
y nunca impide iniciar sesión: si la entrada no se puede registrar, se avisa y se sigue.
"""
import logging

from django.contrib import messages
from django.contrib.auth.signals import user_logged_in
from django.core.exceptions import ValidationError
from django.dispatch import receiver
from django.utils import timezone

from apps.core.models import Rol

from .models import RegistroAsistencia

logger = logging.getLogger(__name__)

# Roles que marcan asistencia al iniciar sesión. El administrador no "llega" por el sistema.
ROLES_QUE_MARCAN = {Rol.VENDEDOR, Rol.ENCARGADO_CIERRE}


def _regreso_de_almuerzo(request, empleado, momento):
    """El primer inicio de sesión después de «Salir a almorzar» registra el regreso (nunca bloquea el ingreso)."""
    from .servicios import registrar_regreso_almuerzo, tolerancia_almuerzo

    try:
        resultado = registrar_regreso_almuerzo(empleado, momento)
    except Exception:
        logger.exception("No se pudo registrar el regreso del almuerzo de %s", empleado)
        return
    if resultado is None or request is None:
        return
    _, exceso = resultado
    hora = timezone.localtime(momento).strftime("%H:%M")
    if exceso > tolerancia_almuerzo():
        messages.warning(request, f"Regreso del almuerzo registrado a las {hora}. Se pasó {exceso} min de su hora "
                                  "de almuerzo; se informó al administrador.", fail_silently=True)
    else:
        messages.success(request, f"Regreso del almuerzo registrado a las {hora}.", fail_silently=True)


@receiver(user_logged_in, dispatch_uid="asistencia_entrada_al_iniciar_sesion")
def registrar_entrada_al_iniciar_sesion(sender, request, user, **kwargs):
    if user.rol not in ROLES_QUE_MARCAN:
        return
    empleado = getattr(user, "empleado", None)
    if empleado is None:
        return
    momento = timezone.now()  # hora exacta del servidor, con segundos: no la puede alterar el usuario
    if RegistroAsistencia.objects.filter(empleado=empleado, fecha=timezone.localtime(momento).date()).exists():
        _regreso_de_almuerzo(request, empleado, momento)  # ya marcó entrada hoy: ¿vuelve del almuerzo?
        return

    from .servicios import registrar_entrada

    try:
        registro = registrar_entrada(empleado, momento, usuario=user, origen=RegistroAsistencia.Origen.SISTEMA)
    except ValidationError as error:
        # Ej.: empleado de vacaciones o con licencia. Se informa sin bloquear el ingreso.
        if request is not None:
            messages.info(request, "No se registró su entrada: " + " ".join(error.messages), fail_silently=True)
        return
    except Exception:  # la asistencia jamás debe impedir iniciar sesión
        logger.exception("No se pudo registrar la entrada automática de %s", user)
        return
    if request is not None:
        hora = timezone.localtime(registro.entrada).strftime("%H:%M:%S")
        messages.success(request, f"Entrada registrada a las {hora}.", fail_silently=True)
