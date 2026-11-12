from .modulos import menu_de
from .models import Notificacion, Rol


def contadores(usuario) -> dict:
    """Contadores del menú y la campana. Se refrescan solos cada 30 s (vista core.contadores)."""
    datos = {"notificaciones_sin_leer": Notificacion.objects.filter(destinatario=usuario, leida=False).count()}
    from apps.mensajes.acceso import puede_usar

    if puede_usar(usuario):  # los asesores de ventas no tienen mensajería
        from apps.mensajes.servicios import sin_leer

        datos["mensajes_sin_leer"] = sin_leer(usuario)
    if usuario.has_perm("cierre.add_gestioncierre"):
        from apps.ventas.models import FormularioDAMI

        datos["dami_sin_asignar"] = FormularioDAMI.objects.filter(estado=FormularioDAMI.Estado.ENVIADO).count()
    if usuario.has_perm("ventas.change_solicitudbase"):
        from apps.ventas.servicios import solicitudes_pendientes

        datos["solicitudes_base_pendientes"] = solicitudes_pendientes()
    if usuario.rol == Rol.VENDEDOR:
        # Aviso del break en curso (con «Volví del break»): llega con el sondeo de la campana.
        from apps.asistencia.breaks import pendiente_de, proximo_para

        empleado = getattr(usuario, "empleado", None)
        datos["avisa_breaks"] = True
        datos["break_pendiente"] = pendiente_de(empleado)
        # Break programado por empezar (ya se envió el recordatorio): cuenta atrás hasta que empiece.
        datos["break_proximo"] = None if datos["break_pendiente"] else proximo_para(empleado)
    return datos


def _jornada_abierta(usuario):
    """Registro de hoy sin salida de quien marca asistencia: muestra el botón «Finalizar día»."""
    from apps.asistencia.signals import ROLES_QUE_MARCAN

    if usuario.rol not in ROLES_QUE_MARCAN:
        return None
    from apps.asistencia.servicios import jornada_abierta

    return jornada_abierta(getattr(usuario, "empleado", None))


def _almuerzo_en_curso(request):
    """
    Pantalla de acceso tras «Salir a almorzar»: datos del contador (cookie firmada con el registro).
    Desaparece en cuanto la persona vuelve a iniciar sesión (el registro ya tiene almuerzo_fin).
    """
    from apps.asistencia.models import RegistroAsistencia
    from apps.asistencia.servicios import duracion_almuerzo, regreso_esperado, tolerancia_almuerzo
    from apps.asistencia.views import COOKIE_ALMUERZO, SAL_COOKIE_ALMUERZO

    pk = request.get_signed_cookie(COOKIE_ALMUERZO, default=None, salt=SAL_COOKIE_ALMUERZO)
    if not pk or not str(pk).isdigit():
        return None
    from django.utils import timezone

    registro = RegistroAsistencia.objects.select_related("empleado").filter(
        pk=pk, fecha=timezone.localdate(), almuerzo_inicio__isnull=False, almuerzo_fin__isnull=True, salida__isnull=True,
    ).first()
    if registro is None:
        return None
    return {
        "empleado": registro.empleado,
        "inicio": registro.almuerzo_inicio,
        "regreso": regreso_esperado(registro),
        "minutos": int(duracion_almuerzo().total_seconds() // 60),
        "tolerancia": tolerancia_almuerzo(),
    }


def navegacion(request):
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {"almuerzo_en_curso": _almuerzo_en_curso(request)} if usuario is not None else {}
    from .push import clave_publica

    contexto = {"menu_secciones": menu_de(usuario), "vapid_publica": clave_publica()} | contadores(usuario)
    contexto["jornada_abierta"] = jornada = _jornada_abierta(usuario)
    # «Salir a almorzar»: una vez por día, con la jornada abierta.
    contexto["puede_almorzar"] = jornada is not None and jornada.almuerzo_inicio is None
    if usuario.has_perm("asistencia.view_novedadasistencia"):
        from apps.asistencia.models import NovedadAsistencia

        contexto["novedades_pendientes"] = NovedadAsistencia.objects.filter(estado=NovedadAsistencia.Estado.PENDIENTE).count()
    return contexto
