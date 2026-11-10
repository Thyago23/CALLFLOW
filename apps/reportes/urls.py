from django.urls import path

from . import views

app_name = "reportes"

urlpatterns = [
    path("", views.centro, name="centro"),
    path("estadisticas/", views.estadisticas, name="estadisticas"),
    path("estadisticas/pdf/", views.estadisticas_descarga, name="estadisticas_pdf"),
    path("<str:codigo>/", views.reporte, name="reporte"),
    path("<str:codigo>/pdf/", views.reporte_pdf, name="reporte_pdf"),
]
