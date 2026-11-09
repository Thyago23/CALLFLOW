"""
Notificaciones en pantalla (avisos flotantes en la esquina inferior izquierda).

La página consulta cada pocos segundos (vista core.contadores). En cada consulta:
- se generan los avisos de agenda que ya tocan, aunque la persona no cambie de página;
- se devuelven las notificaciones nuevas que aún no se mostraron en esta sesión.
"""
from datetime import timedelta

from django.utils import timezone

from .models import Notificacion

CLAVE_SESION = "notificaciones_ultima_mostrada"
VENTANA_INICIAL = timedelta(minutes=10)  # al iniciar sesión, solo se muestran las sin leer recientes
MAXIMO_POR_CONSULTA = 4

ICONOS = {
    Notificacion.Tipo.AGENDA: "calendar-event",
    Notificacion.Tipo.STOCK_BAJO: "box-seam",
    Notificacion.Tipo.SOLICITUD_BASE: "database",
    Notificacion.Tipo.FORMULARIO_DAMI: "file-earmark-text",
    Notificacion.Tipo.NOMINA: "cash-coin",
    Notificacion.Tipo.GENERAL: "bell",
}


def generar_pendientes(usuario) -> None:
    """Crea los avisos de agenda que ya llegaron (la consulta periódica no pasa por el middleware)."""
    if usuario.has_perm("agenda.view_evento"):
        from apps.agenda.servicios import generar_avisos

        generar_avisos(usuario=usuario)


def nuevas_para_mostrar(request) -> list[Notificacion]:
    """Notificaciones sin leer que todavía no aparecieron en pantalla en esta sesión (las más antiguas primero)."""
    usuario = request.user
    ultima = request.session.get(CLAVE_SESION)
    pendientes = Notificacion.objects.filter(destinatario=usuario, leida=False)
    if ultima is None:
        pendientes = pendientes.filter(creado_en__gte=timezone.now() - VENTANA_INICIAL)
    else:
        pendientes = pendientes.filter(pk__gt=ultima)
    nuevas = list(pendientes.order_by("-pk")[:MAXIMO_POR_CONSULTA])[::-1]
    ultima_existente = Notificacion.objects.filter(destinatario=usuario).order_by("-pk").values_list("pk", flat=True).first()
    request.session[CLAVE_SESION] = max(ultima or 0, ultima_existente or 0)
    for n in nuevas:
        n.icono = ICONOS.get(n.tipo, "bell")
        n.fija = n.tipo == Notificacion.Tipo.AGENDA  # los recordatorios se quedan hasta cerrarlos
    return nuevas
