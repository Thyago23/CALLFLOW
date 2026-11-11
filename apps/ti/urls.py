from django.urls import path

from . import views

app_name = "ti"

urlpatterns = [
    path("", views.equipos, name="equipos"),
    path("equipos/nuevo/", views.equipo_crear, name="crear"),
    path("equipos/<int:pk>/", views.equipo_detalle, name="detalle"),
    path("equipos/<int:pk>/editar/", views.equipo_editar, name="editar"),
    path("equipos/<int:pk>/evento/", views.evento_crear, name="evento"),
    path("conectados/", views.conectados, name="conectados"),
    path("conectados/<int:pk>/cerrar/", views.cerrar_sesion, name="cerrar_sesion"),
]
