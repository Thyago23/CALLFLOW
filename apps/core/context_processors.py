from .modulos import menu_de
from .models import Notificacion


def navegacion(request):
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    return {
        "menu_secciones": menu_de(usuario),
        "notificaciones_sin_leer": Notificacion.objects.filter(destinatario=usuario, leida=False).count(),
    }
