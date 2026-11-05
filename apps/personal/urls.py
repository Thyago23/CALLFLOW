from django.urls import path

from . import views

app_name = "personal"

urlpatterns = [
    path("empleados/", views.EmpleadoListaView.as_view(), name="empleado_lista"),
    path("empleados/nuevo/", views.EmpleadoCrearView.as_view(), name="empleado_crear"),
    path("empleados/verificar-identificacion/", views.verificar_identificacion, name="verificar_identificacion"),
]
