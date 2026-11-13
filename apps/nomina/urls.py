from django.urls import path

from . import views

app_name = "nomina"

urlpatterns = [
    path("", views.resumen, name="resumen"),
    path("rol/<int:empleado_pk>/", views.rol, name="rol"),
    path("rol/<int:empleado_pk>/pdf/", views.rol_pdf, name="rol_pdf"),
    path("rol/<int:empleado_pk>/comision/pdf/", views.comision_pdf, name="comision_pdf"),
    path("llamados/<int:novedad_pk>/acta/", views.acta_llamado_pdf, name="acta_llamado"),
    path("reportes/rol-de-pagos/", views.reporte_roles, name="reporte_roles"),
    path("caja-chica/", views.caja_chica, name="caja_chica"),
    path("caja-chica/<int:pk>/anular/", views.caja_anular, name="caja_anular"),
    path("caja-chica/reporte/", views.caja_reporte, name="caja_reporte"),
    path("caja-chica/fondo/", views.caja_fondo, name="caja_fondo"),
    path("caja-chica/<int:pk>/recibo/", views.caja_recibo, name="caja_recibo"),
    path("caja-chica/recibos/", views.caja_recibos, name="caja_recibos"),
    path("caja-chica/recibos/<int:pk>/", views.caja_recibo_entrega, name="caja_recibo_entrega"),
    path("caja-chica/arqueo/", views.caja_arqueo, name="caja_arqueo"),
    path("caja-chica/arqueo/<int:pk>/", views.caja_arqueo_detalle, name="caja_arqueo_detalle"),
]
