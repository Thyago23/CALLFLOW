import time

from .servicios import generar_avisos

_INTERVALO_SEGUNDOS = 60
_CLAVE_SESION = "agenda_ultimo_aviso"


class AvisosAgendaMiddleware:
    """
    Revisa los avisos de agenda de la persona mientras navega (como máximo una vez por minuto),
    para que reciba sus recordatorios aunque no esté programado el comando enviar_avisos_agenda.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, "user", None)
        if (usuario is not None and usuario.is_authenticated and request.method == "GET"
                and not request.headers.get("HX-Request") and usuario.has_perm("agenda.view_evento")):
            ahora = time.time()
            if ahora - request.session.get(_CLAVE_SESION, 0) >= _INTERVALO_SEGUNDOS:
                request.session[_CLAVE_SESION] = ahora
                generar_avisos(usuario=usuario)
        return self.get_response(request)
