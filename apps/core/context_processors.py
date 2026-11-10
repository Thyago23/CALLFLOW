from .modulos import menu_de
from .models import Notificacion


def contadores(usuario) -> dict:
    """Contadores del menú y la campana. Se refrescan solos cada 30 s (vista core.contadores)."""
    datos = {"notificaciones_sin_leer": Notificacion.objects.filter(destinatario=usuario, leida=False).count()}
    if usuario.has_perm("cierre.add_gestioncierre"):
        from apps.ventas.models import FormularioDAMI

        datos["dami_sin_asignar"] = FormularioDAMI.objects.filter(estado=FormularioDAMI.Estado.ENVIADO).count()
    if usuario.has_perm("ventas.change_solicitudbase"):
        from apps.ventas.servicios import solicitudes_pendientes

        datos["solicitudes_base_pendientes"] = solicitudes_pendientes()
    return datos


def navegacion(request):
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    from .push import clave_publica

    contexto = {"menu_secciones": menu_de(usuario), "vapid_publica": clave_publica()} | contadores(usuario)
    if usuario.has_perm("asistencia.view_novedadasistencia"):
        from apps.asistencia.models import NovedadAsistencia

        contexto["novedades_pendientes"] = NovedadAsistencia.objects.filter(estado=NovedadAsistencia.Estado.PENDIENTE).count()
    return contexto
