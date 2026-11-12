"""
Mensajería interna: solo TI, administradores y supervisores. Los asesores de ventas no tienen acceso.

Va por rol (como el módulo de TI) y no por permisos de Django, a propósito: así nadie le «da» acceso a un
asesor por error. La base de datos repite la regla (migración mensajes/0002): un asesor no puede ser
participante de una conversación ni escribir en ella.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from apps.core.models import Rol

ROLES_MENSAJERIA = (Rol.ADMINISTRADOR, Rol.ENCARGADO_CIERRE, Rol.TI)


def puede_usar(usuario) -> bool:
    return bool(usuario and usuario.is_authenticated and usuario.is_active and usuario.rol in ROLES_MENSAJERIA)


def requiere_mensajeria(vista):
    @login_required
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not puede_usar(request.user):
            raise PermissionDenied("La mensajería interna es solo para TI, administradores y supervisores.")
        return vista(request, *args, **kwargs)
    return envoltura
