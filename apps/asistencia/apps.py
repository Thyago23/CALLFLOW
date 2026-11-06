from django.apps import AppConfig


class AsistenciaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.asistencia"
    label = "asistencia"
    verbose_name = "Asistencia"

    def ready(self):
        from . import signals  # noqa: F401  (entrada automática al iniciar sesión)
