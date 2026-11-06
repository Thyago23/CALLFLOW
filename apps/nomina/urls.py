from django.urls import path

from . import views

app_name = "nomina"

urlpatterns = [
    path("", views.resumen, name="resumen"),
    path("rol/<int:empleado_pk>/", views.rol, name="rol"),
    path("rol/<int:empleado_pk>/pdf/", views.rol_pdf, name="rol_pdf"),
]
