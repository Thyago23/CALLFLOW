"""
Empresa activa (Multivisas o T&T): la contadora trabaja en una a la vez y todo lo que ve y registra es de esa.
Se recuerda en la sesión; se cambia con el selector de la cabecera del módulo.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Empresa

CLAVE_SESION = "contabilidad_empresa"


def empresas():
    return Empresa.objects.filter(activa=True).order_by("nombre")


def empresa_activa(request) -> Empresa | None:
    lista = list(empresas())
    elegida = next((e for e in lista if e.pk == request.session.get(CLAVE_SESION)), None)
    return elegida or (lista[0] if lista else None)


def fijar(request, empresa: Empresa):
    request.session[CLAVE_SESION] = empresa.pk


def con_empresa(*permisos: str):
    """login + permisos + `request.empresa` (la empresa activa). Sin empresas activas no hay a dónde entrar."""
    def decorador(vista):
        @login_required
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not request.user.has_perms(permisos):
                raise PermissionDenied("No tiene acceso a esta sección.")
            request.empresa = empresa_activa(request)
            if request.empresa is None:
                raise PermissionDenied("No hay empresas activas en la contabilidad.")
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador
