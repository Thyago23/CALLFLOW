"""Tablero de ventas: entrada propia en el menú (Comercial), por eso su propio espacio de nombres."""
from django.urls import path

from . import views

app_name = "tablero"

urlpatterns = [
    path("", views.tablero_ventas, name="ventas"),
]
