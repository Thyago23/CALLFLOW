from django.contrib import admin

from .models import (ActivoFijo, Asiento, Cliente, CuentaContable, CuentaFinanciera, Empresa, Lead, LineaAsiento,
                     MovimientoFinanciero, Proveedor)


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "codigo", "razon_social", "ruc", "activa")


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "tipo", "naturaleza", "activa")
    list_filter = ("tipo", "naturaleza", "activa")
    search_fields = ("codigo", "nombre")


@admin.register(CuentaFinanciera)
class CuentaFinancieraAdmin(admin.ModelAdmin):
    list_display = ("nombre", "empresa", "tipo", "banco", "saldo_inicial", "fecha_saldo_inicial", "activa")
    list_filter = ("empresa", "tipo")


@admin.register(MovimientoFinanciero)
class MovimientoFinancieroAdmin(admin.ModelAdmin):
    """Solo consulta: los movimientos se registran y anulan desde el módulo (la base impide editarlos)."""

    list_display = ("fecha", "cuenta", "tipo", "concepto", "valor", "conciliado", "anulado")
    list_filter = ("cuenta__empresa", "tipo", "conciliado", "anulado")
    search_fields = ("concepto", "referencia", "tercero")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LineaInline(admin.TabularInline):
    model = LineaAsiento
    extra = 0
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Asiento)
class AsientoAdmin(admin.ModelAdmin):
    """Solo consulta: los asientos se registran desde el libro diario."""

    list_display = ("numero", "empresa", "fecha", "tipo", "concepto", "estado")
    list_filter = ("empresa", "estado", "tipo")
    search_fields = ("concepto", "documento")
    inlines = [LineaInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombres", "empresa", "linea", "identificacion", "activo")
    list_filter = ("empresa", "linea", "activo")
    search_fields = ("nombres", "identificacion", "codigo")


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "empresa", "tipo", "identificacion", "activo")
    list_filter = ("empresa", "tipo", "activo")
    search_fields = ("nombre", "identificacion", "codigo")


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("nombres", "empresa", "interes", "origen", "estado", "responsable", "proximo_contacto")
    list_filter = ("empresa", "estado", "interes", "origen")
    search_fields = ("nombres", "telefono", "correo")


@admin.register(ActivoFijo)
class ActivoFijoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "descripcion", "empresa", "cantidad", "valor_unitario", "estado")
    list_filter = ("empresa", "estado")
    search_fields = ("codigo", "descripcion", "custodio")
