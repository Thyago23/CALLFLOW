from .modulos import menu_de
from .models import Notificacion, Usuario


def navegacion(request):
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    contexto = {
        "menu_secciones": menu_de(usuario),
        "notificaciones_sin_leer": Notificacion.objects.filter(destinatario=usuario, leida=False).count(),
    }
    if usuario.has_perm("core.change_usuario"):
        contexto["solicitudes_pendientes"] = Usuario.objects.filter(is_active=False, aprobado_en__isnull=True).count()
    return contexto
