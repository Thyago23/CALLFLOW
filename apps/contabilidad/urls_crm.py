from django.urls import path

from . import views_crm as views

app_name = "crm"

urlpatterns = [
    path("", views.leads, name="leads"),
    path("leads/nuevo/", views.lead_crear, name="lead_crear"),
    path("leads/<int:pk>/", views.lead_detalle, name="lead_detalle"),
    path("leads/<int:pk>/editar/", views.lead_editar, name="lead_editar"),
    path("leads/<int:pk>/seguimiento/", views.lead_seguimiento, name="lead_seguimiento"),
    path("leads/<int:pk>/convertir/", views.lead_convertir, name="lead_convertir"),
    path("clientes/", views.clientes, name="clientes"),
    path("clientes/nuevo/", views.cliente_crear, name="cliente_crear"),
    path("clientes/<int:pk>/", views.cliente_detalle, name="cliente_detalle"),
    path("clientes/<int:pk>/editar/", views.cliente_editar, name="cliente_editar"),
    path("proveedores/", views.proveedores, name="proveedores"),
    path("proveedores/nuevo/", views.proveedor_crear, name="proveedor_crear"),
    path("proveedores/<int:pk>/", views.proveedor_detalle, name="proveedor_detalle"),
    path("proveedores/<int:pk>/editar/", views.proveedor_editar, name="proveedor_editar"),
]
