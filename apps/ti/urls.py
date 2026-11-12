from django.urls import path

from . import views

app_name = "ti"

urlpatterns = [
    path("", views.equipos, name="equipos"),
    path("equipos/nuevo/", views.equipo_crear, name="crear"),
    path("equipos/<int:pk>/", views.equipo_detalle, name="detalle"),
    path("equipos/<int:pk>/editar/", views.equipo_editar, name="editar"),
    path("equipos/<int:pk>/evento/", views.evento_crear, name="evento"),
    path("equipos/<int:pk>/entregar/", views.entregar, name="entregar"),
    path("equipos/<int:pk>/devolver/", views.devolver, name="devolver"),
    path("prestamos/", views.prestamos, name="prestamos"),
    path("actas/<int:pk>/pdf/", views.acta_pdf, name="acta_pdf"),
    path("actas/<int:pk>/firmada/", views.acta_firmada_ver, name="acta_firmada"),
    path("actas/<int:pk>/firmada/subir/", views.acta_firmada_subir, name="acta_firmada_subir"),
    path("conectados/", views.conectados, name="conectados"),
    path("servidor/", views.servidor, name="servidor"),
    path("conectados/<int:pk>/cerrar/", views.cerrar_sesion, name="cerrar_sesion"),
]
