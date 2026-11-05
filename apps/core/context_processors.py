from .modulos import menu_de
from .models import Notificacion


def navegacion(request):
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    contexto = {
        "menu_secciones": menu_de(usuario),
        "notificaciones_sin_leer": Notificacion.objects.filter(destinatario=usuario, leida=False).count(),
    }
    if usuario.has_perm("asistencia.view_novedadasistencia"):
        from apps.asistencia.models import NovedadAsistencia

        contexto["novedades_pendientes"] = NovedadAsistencia.objects.filter(estado=NovedadAsistencia.Estado.PENDIENTE).count()
    return contexto
