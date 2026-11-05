from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="inicio"),
    path("cuentas/ingresar/", views.IngresoView.as_view(), name="login"),
    path("cuentas/salir/", auth_views.LogoutView.as_view(), name="logout"),
    path("cuentas/registro/", views.registro, name="registro"),
    path("cuentas/solicitudes/", views.solicitudes_acceso, name="solicitudes_acceso"),
    path("cuentas/solicitudes/<int:pk>/aprobar/", views.aprobar_solicitud, name="aprobar_solicitud"),
    path("cuentas/solicitudes/<int:pk>/rechazar/", views.rechazar_solicitud, name="rechazar_solicitud"),
    path("notificaciones/marcar-leidas/", views.marcar_notificaciones_leidas, name="notificaciones_leidas"),
]
