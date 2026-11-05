from django.contrib import admin
from django.urls import path

admin.site.site_header = "CALLFLOW - Administración"
admin.site.site_title = "CALLFLOW"

urlpatterns = [
    path("admin/", admin.site.urls),
]
