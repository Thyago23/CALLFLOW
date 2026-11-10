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
    path("hoja-de-produccion/<int:pk>/aprobacion/", views.hoja_aprobacion, name="hoja_aprobacion"),
    path("hoja-de-produccion/vendedores/", views.hojas_vendedores, name="hojas_vendedores"),
    path("hoja-de-produccion/vendedores/<int:pk>/", views.hoja_vendedor, name="hoja_vendedor"),
    path("validar-tarjeta/", views.validar_tarjeta, name="validar_tarjeta"),
    path("validar-tarjeta/catalogo/", views.catalogo_bin, name="catalogo_bin"),
    path("validar-tarjeta/catalogo/<int:pk>/eliminar/", views.eliminar_bin, name="eliminar_bin"),
    path("bases/", views.bases, name="bases"),
    path("bases/plantilla/", views.bases_plantilla, name="bases_plantilla"),
    path("bases/seguimiento/", views.bases_seguimiento, name="bases_seguimiento"),
    path("bases/contactos/", views.bases_contactos, name="bases_contactos"),
    path("bases/repetidos/", views.repetidos, name="repetidos"),
    path("bases/repetidos/<int:pk>/revisar/", views.repetido_revisar, name="repetido_revisar"),
    path("bases/<int:pk>/", views.base_detalle, name="base_detalle"),
    path("bases/<int:pk>/excel/", views.base_excel, name="base_excel"),
    path("mis-contactos/", views.mis_contactos, name="mis_contactos"),
    path("mis-estadisticas/", views.mis_estadisticas, name="mis_estadisticas"),
    path("mis-contactos/<int:pk>/categorizar/", views.contacto_categorizar, name="contacto_categorizar"),
    path("bases/solicitudes/<int:pk>/responder/", views.responder_solicitud, name="responder_solicitud"),
]
