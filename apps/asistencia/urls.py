from django.urls import path

from . import views

app_name = "asistencia"

urlpatterns = [
    path("", views.tablero, name="tablero"),
    path("empleado/<int:pk>/entrada/", views.marcar_entrada, name="entrada"),
    path("empleado/<int:pk>/salida/", views.marcar_salida, name="salida"),
    path("empleado/<int:pk>/ausente/", views.marcar_ausente, name="ausente"),
    path("ausencias-del-dia/", views.ausencias_del_dia, name="ausencias_del_dia"),
    path("registro-manual/", views.registro_manual, name="registro_manual"),
    path("novedades/", views.novedades, name="novedades"),
    path("novedades/<int:pk>/clasificar/", views.clasificar_novedad, name="clasificar"),
    path("novedades/<int:pk>/reabrir/", views.reabrir_novedad, name="reabrir"),
    path("historial/", views.historial, name="historial"),
    path("horarios/", views.horarios, name="horarios"),
    path("finalizar-dia/", views.finalizar_dia, name="finalizar_dia"),
    path("salir-almuerzo/", views.salir_almuerzo, name="salir_almuerzo"),
    path("breaks/", views.breaks, name="breaks"),
    path("breaks/panel/", views.breaks_panel, name="breaks_panel"),
    path("breaks/volvi/", views.volvi_del_break, name="volvi_break"),
]
