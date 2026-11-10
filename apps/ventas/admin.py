from django.apps import apps
from django.contrib import admin

# La tabla de tarjetas no se registra: el número cifrado no debe verse ni editarse desde /admin.
_EXCLUIDOS = {"tarjetadami"}

for modelo in apps.get_app_config("ventas").get_models():
    if modelo._meta.managed and modelo._meta.model_name not in _EXCLUIDOS:
        admin.site.register(modelo)
