"""
Acceso al módulo de TI: solo las cuentas con rol «Tecnología (TI)».

Va por rol y no por permisos a propósito: un superusuario o el administrador tienen todos los permisos
de Django, pero el módulo de TI es del departamento de TI (y TI, a su vez, no ve lo del administrador).
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from apps.core.models import Rol


def es_ti(usuario) -> bool:
    return bool(usuario and usuario.is_authenticated and usuario.is_active and usuario.rol == Rol.TI)


def requiere_ti(vista):
    @login_required
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not es_ti(request.user):
            raise PermissionDenied("Esta sección es del departamento de TI.")
        return vista(request, *args, **kwargs)
    return envoltura
