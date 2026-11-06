from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="inicio"),
    path("cuentas/ingresar/", views.IngresoView.as_view(), name="login"),
    path("cuentas/salir/", auth_views.LogoutView.as_view(), name="logout"),
    path("cuentas/cambiar-clave/", views.cambiar_clave, name="cambiar_clave"),
    path("usuarios/", views.usuarios, name="usuarios"),
    path("usuarios/nuevo/", views.usuario_crear, name="usuario_crear"),
    path("usuarios/credenciales/", views.usuario_credenciales, name="usuario_credenciales"),
    path("usuarios/<int:pk>/restablecer-clave/", views.usuario_restablecer_clave, name="usuario_restablecer_clave"),
    path("usuarios/<int:pk>/cambiar-estado/", views.usuario_cambiar_estado, name="usuario_cambiar_estado"),
    path("notificaciones/marcar-leidas/", views.marcar_notificaciones_leidas, name="notificaciones_leidas"),
    path("notificaciones/contadores/", views.contadores, name="contadores"),
]
