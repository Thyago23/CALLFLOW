from django.urls import path

from . import views

app_name = "chips"

urlpatterns = [
    path("", views.chips, name="lista"),
    path("nuevo/", views.chip_crear, name="crear"),
    path("<int:pk>/", views.chip_detalle, name="detalle"),
    path("<int:pk>/editar/", views.chip_editar, name="editar"),
    path("<int:pk>/reposicion/", views.reposicion_crear, name="reposicion"),
    path("reporte/", views.reporte, name="reporte"),
]
