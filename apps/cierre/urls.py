from django.urls import path

from . import views

app_name = "cierre"

urlpatterns = [
    path("", views.bandeja, name="bandeja"),
    path("formularios/<int:pk>/", views.formulario, name="formulario"),
    path("formularios/<int:pk>/accion/", views.accion, name="accion"),
    path("formularios/<int:pk>/resultado/", views.resultado, name="resultado"),
    path("formularios/<int:pk>/corregir/", views.corregir, name="corregir"),
    path("responsables/", views.responsables, name="responsables"),
    path("metas/", views.metas, name="metas"),
    path("panel/", views.panel, name="panel"),
    path("panel/ocultar-valores/", views.ocultar_valores, name="ocultar_valores"),
]
