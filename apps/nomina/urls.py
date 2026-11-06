from django.urls import path

from . import views

app_name = "nomina"

urlpatterns = [
    path("", views.resumen, name="resumen"),
    path("rol/<int:empleado_pk>/", views.rol, name="rol"),
    path("rol/<int:empleado_pk>/pdf/", views.rol_pdf, name="rol_pdf"),
    path("reportes/rol-de-pagos/", views.reporte_roles, name="reporte_roles"),
    path("caja-chica/", views.caja_chica, name="caja_chica"),
    path("caja-chica/<int:pk>/anular/", views.caja_anular, name="caja_anular"),
    path("caja-chica/reporte/", views.caja_reporte, name="caja_reporte"),
]
