from django.urls import path

from . import views

app_name = "mensajes"

urlpatterns = [
    path("", views.bandeja, name="bandeja"),
    path("lista/", views.lista, name="lista"),
    path("directo/", views.nueva_directa, name="nueva_directa"),
    path("grupo/", views.nuevo_grupo, name="nuevo_grupo"),
    path("<int:pk>/", views.conversacion, name="conversacion"),
    path("<int:pk>/nuevos/", views.nuevos, name="nuevos"),
    path("<int:pk>/enviar/", views.enviar, name="enviar"),
    path("<int:pk>/agregar/", views.agregar, name="agregar"),
    path("<int:pk>/salir/", views.salir, name="salir"),
    path("mensaje/<int:pk>/eliminar/", views.eliminar, name="eliminar"),
    path("mensaje/<int:pk>/adjunto/", views.adjunto, name="adjunto"),
]
