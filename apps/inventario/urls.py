from django.urls import path

from . import views

app_name = "inventario"

urlpatterns = [
    path("", views.materiales, name="materiales"),
    path("materiales/nuevo/", views.material_crear, name="material_crear"),
    path("materiales/<int:pk>/", views.material_detalle, name="material_detalle"),
    path("materiales/<int:pk>/editar/", views.material_editar, name="material_editar"),
    path("materiales/<int:pk>/estado/", views.material_estado, name="material_estado"),
    path("movimientos/", views.movimientos, name="movimientos"),
    path("movimientos/nuevo/", views.movimiento_crear, name="movimiento_crear"),
]
