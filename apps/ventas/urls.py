from django.urls import path

from . import views

app_name = "ventas"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("formularios/", views.formularios, name="formularios"),
    path("formularios/nuevo/", views.formulario_nuevo, name="formulario_nuevo"),
    path("formularios/<int:pk>/", views.formulario_detalle, name="formulario_detalle"),
    path("formularios/<int:pk>/editar/", views.formulario_editar, name="formulario_editar"),
    path("formularios/<int:pk>/accion/", views.formulario_accion, name="formulario_accion"),
    path("clientes/buscar/", views.buscar_cliente, name="buscar_cliente"),
    path("hoja-de-produccion/", views.hoja, name="hoja"),
    path("bases/", views.bases, name="bases"),
    path("bases/solicitudes/<int:pk>/responder/", views.responder_solicitud, name="responder_solicitud"),
]
