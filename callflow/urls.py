from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "CALLFLOW - Administración"
admin.site.site_title = "CALLFLOW"

urlpatterns = [
    path("", include("apps.core.urls")),
    path("personal/", include("apps.personal.urls")),
    path("asistencia/", include("apps.asistencia.urls")),
    path("inventario/", include("apps.inventario.urls")),
    path("agenda/", include("apps.agenda.urls")),
    path("ventas/", include("apps.ventas.urls")),
    path("cierre/", include("apps.cierre.urls")),
    path("documentos/", include("apps.documentos.urls")),
    path("nomina/", include("apps.nomina.urls")),
    path("admin/", admin.site.urls),
]
