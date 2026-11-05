from django.urls import path

from . import views

app_name = "personal"

urlpatterns = [
    path("empleados/", views.EmpleadoListaView.as_view(), name="empleado_lista"),
    path("empleados/nuevo/", views.EmpleadoCrearView.as_view(), name="empleado_crear"),
    path("empleados/<int:pk>/", views.EmpleadoDetalleView.as_view(), name="empleado_detalle"),
    path("empleados/verificar-identificacion/", views.verificar_identificacion, name="verificar_identificacion"),
    path("empleados/leer-cv/", views.leer_cv, name="leer_cv"),
    path("empleados/<int:pk>/crear-cuenta/", views.empleado_crear_cuenta, name="empleado_crear_cuenta"),
    path("empleados/<int:pk>/observaciones/", views.observacion_crear, name="observacion_crear"),
    path("documentos/<int:pk>/", views.documento_ver, name="documento_ver"),
]
