from django.urls import path

from . import views_soporte as views

app_name = "soporte"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("nuevo/", views.crear, name="crear"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("<int:pk>/gestionar/", views.gestionar, name="gestionar"),
    path("<int:pk>/confirmar/", views.confirmar, name="confirmar"),
]
