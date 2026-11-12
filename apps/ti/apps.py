from django.apps import AppConfig


class TiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ti"
    label = "ti"
    verbose_name = "Tecnología (TI)"

    def ready(self):
        from . import signals  # noqa: F401  (registra las conexiones al iniciar y cerrar sesión)
