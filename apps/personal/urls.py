from django.urls import path

from . import views

app_name = "personal"

urlpatterns = [
    path("empleados/", views.EmpleadoListaView.as_view(), name="empleado_lista"),
    path("empleados/nuevo/", views.EmpleadoCrearView.as_view(), name="empleado_crear"),
    path("empleados/<int:pk>/", views.EmpleadoDetalleView.as_view(), name="empleado_detalle"),
    path("empleados/<int:pk>/editar/", views.EmpleadoEditarView.as_view(), name="empleado_editar"),
    path("empleados/verificar-identificacion/", views.verificar_identificacion, name="verificar_identificacion"),
    path("empleados/leer-cv/", views.leer_cv, name="leer_cv"),
    path("empleados/<int:pk>/salida/", views.empleado_salida, name="empleado_salida"),
    path("empleados/<int:pk>/reintegrar/", views.empleado_reintegrar, name="empleado_reintegrar"),
    path("empleados/<int:pk>/crear-cuenta/", views.empleado_crear_cuenta, name="empleado_crear_cuenta"),
    path("empleados/<int:pk>/observaciones/", views.observacion_crear, name="observacion_crear"),
    path("empleados/<int:pk>/sueldo/", views.salario_cambiar, name="salario_cambiar"),
    path("empleados/<int:pk>/equipo/", views.equipo_cambiar, name="equipo_cambiar"),
    path("documentos/<int:pk>/", views.documento_ver, name="documento_ver"),
]
