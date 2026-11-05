from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Notificacion, RegistroCambio, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "rol", "is_active")
    list_filter = ("rol", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (("CALLFLOW", {"fields": ("rol",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("CALLFLOW", {"fields": ("email", "rol")}),)


@admin.register(RegistroCambio)
class RegistroCambioAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tabla", "registro_id", "accion", "usuario_id")
    list_filter = ("tabla", "accion")

    # La bitácora es de solo lectura.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Notificacion)
