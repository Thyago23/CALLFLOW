from django.urls import path

from . import views

app_name = "documentos"

urlpatterns = [
    path("<int:pk>/", views.ver_documento, name="ver"),
]
