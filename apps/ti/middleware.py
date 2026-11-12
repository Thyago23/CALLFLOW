import logging

from . import servicios

logger = logging.getLogger(__name__)


class ActividadEquiposMiddleware:
    """Mantiene al día la última actividad de cada sesión (para «Equipos conectados»)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        respuesta = self.get_response(request)
        try:
            servicios.registrar_actividad(request)
        except Exception:  # el registro de TI jamás debe romper una página
            logger.exception("No se pudo registrar la actividad de la sesión")
        return respuesta
