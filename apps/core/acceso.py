"""
Permisos «exclusivos»: solo cuentan si están asignados directamente a la cuenta. A propósito NO
basta con ser superusuario ni pertenecer a un grupo (registro de chips, breaks: son de una sola persona).
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def tiene_permiso_directo(usuario, permiso: str) -> bool:
    """`permiso` como «app.codename»."""
    if not (usuario and usuario.is_authenticated and usuario.is_active):
        return False
    cache = getattr(usuario, "_permisos_directos", None)
    if cache is None:
        cache = {f"{app}.{codigo}" for app, codigo in
                 usuario.user_permissions.values_list("content_type__app_label", "codename")}
        usuario._permisos_directos = cache
    return permiso in cache


def requiere_permiso_directo(permiso: str, mensaje: str = "No tiene acceso a esta sección."):
    def decorador(vista):
        @login_required
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not tiene_permiso_directo(request.user, permiso):
                raise PermissionDenied(mensaje)
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador
