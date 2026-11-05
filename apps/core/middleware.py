from django.db import connection


class UsuarioAuditoriaMiddleware:
    """
    Informa a PostgreSQL qué usuario hace cada petición, para que los triggers de
    auditoría (core_registrocambio) y el historial de formularios registren al autor.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, "user", None)
        uid = str(usuario.pk) if usuario is not None and usuario.is_authenticated else ""
        self._fijar(uid)
        try:
            return self.get_response(request)
        finally:
            self._fijar("")

    @staticmethod
    def _fijar(valor: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('callflow.usuario_id', %s, false)", [valor])
