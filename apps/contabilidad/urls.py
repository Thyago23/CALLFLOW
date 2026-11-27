from django.urls import path

from . import views

app_name = "contabilidad"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("empresa/", views.cambiar_empresa, name="cambiar_empresa"),
    # Caja y bancos
    path("cajas/", views.cajas, name="cajas"),
    path("cajas/nueva/", views.caja_crear, name="caja_crear"),
    path("cajas/<int:pk>/", views.caja_detalle, name="caja_detalle"),
    path("cajas/<int:pk>/editar/", views.caja_editar, name="caja_editar"),
    path("movimientos/", views.movimientos, name="movimientos"),
    path("movimientos/nuevo/", views.movimiento_crear, name="movimiento_crear"),
    path("movimientos/traspaso/", views.traspaso_crear, name="traspaso_crear"),
    path("movimientos/<int:pk>/anular/", views.movimiento_anular, name="movimiento_anular"),
    path("movimientos/<int:pk>/conciliar/", views.movimiento_conciliar, name="movimiento_conciliar"),
    path("movimientos/<int:pk>/soporte/", views.movimiento_soporte, name="movimiento_soporte"),
    # Plan de cuentas
    path("plan/", views.plan_cuentas, name="plan_cuentas"),
    path("plan/nueva/", views.cuenta_crear, name="cuenta_crear"),
    path("plan/<int:pk>/editar/", views.cuenta_editar, name="cuenta_editar"),
    # Libro diario
    path("diario/", views.diario, name="diario"),
    path("diario/nuevo/", views.asiento_crear, name="asiento_crear"),
    path("diario/<int:pk>/", views.asiento_detalle, name="asiento_detalle"),
    path("diario/<int:pk>/editar/", views.asiento_editar, name="asiento_editar"),
    path("diario/<int:pk>/contabilizar/", views.asiento_contabilizar, name="asiento_contabilizar"),
    path("diario/<int:pk>/anular/", views.asiento_anular, name="asiento_anular"),
    path("diario/<int:pk>/borrar/", views.asiento_borrar, name="asiento_borrar"),
    path("saldos-iniciales/", views.saldos_iniciales, name="saldos_iniciales"),
    # Balance y mayor
    path("balance/", views.balance, name="balance"),
    path("mayor/<int:pk>/", views.mayor, name="mayor"),
    # Activos fijos
    path("activos/", views.activos, name="activos"),
    path("activos/nuevo/", views.activo_crear, name="activo_crear"),
    path("activos/<int:pk>/editar/", views.activo_editar, name="activo_editar"),
]
