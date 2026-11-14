from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = "Núcleo"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_login_failed

        from . import limite_peticiones, signals  # noqa: F401

        user_login_failed.connect(limite_peticiones.al_fallar_ingreso, dispatch_uid="callflow-ingreso-fallido")
        user_logged_in.connect(limite_peticiones.al_ingresar, dispatch_uid="callflow-ingreso-correcto")
