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
    path("licencias/", views.licencias, name="licencias"),
    path("licencias/nueva/", views.licencia_crear, name="licencia_crear"),
    path("licencias/<int:pk>/editar/", views.licencia_editar, name="licencia_editar"),
    path("mantenimiento/", views.mantenimiento, name="mantenimiento"),
    path("mantenimiento/nuevo/", views.plan_crear, name="plan_crear"),
    path("mantenimiento/<int:pk>/editar/", views.plan_editar, name="plan_editar"),
    path("mantenimiento/<int:pk>/realizado/", views.plan_realizado, name="plan_realizado"),
    path("accesos/", views.accesos, name="accesos"),
    path("accesos/desbloquear/", views.desbloquear, name="desbloquear"),
    path("respaldos/", views.respaldos, name="respaldos"),
]
