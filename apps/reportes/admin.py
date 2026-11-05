from django.apps import apps
from django.contrib import admin

for modelo in apps.get_app_config("reportes").get_models():
    if modelo._meta.managed:
        admin.site.register(modelo)
