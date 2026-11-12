from django.urls import path

from . import views

app_name = "documentos"

urlpatterns = [
    path("<int:pk>/", views.ver_documento, name="ver"),
    path("plantillas/", views.plantillas, name="plantillas"),
    path("plantillas/nueva/", views.plantilla_nueva, name="plantilla_nueva"),
    path("plantillas/<int:pk>/editar/", views.plantilla_editar, name="plantilla_editar"),
    path("plantillas/vista-previa/", views.plantilla_vista_previa, name="plantilla_vista_previa"),
    path("contratos/<int:empleado_pk>/vista-previa/", views.contrato_vista_previa, name="contrato_vista_previa"),
    path("contratos/<int:empleado_pk>/generar/", views.contrato_generar, name="contrato_generar"),
    path("contratos/<int:pk>/revisado/", views.contrato_revisado, name="contrato_revisado"),
]
