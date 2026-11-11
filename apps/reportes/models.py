"""4.5 Reportes y exportación."""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now

from apps.core.models import ModeloBase, Rol


class TipoReporte(ModeloBase):
    class Modulo(models.TextChoices):
        ADMINISTRATIVO = "ADMINISTRATIVO", "Administrativo"
        NOMINA = "NOMINA", "Nómina"
        VENTAS = "VENTAS", "Ventas"
        CIERRE = "CIERRE", "Cierre de ventas"
        TI = "TI", "Tecnología (TI)"

    codigo = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=120)
    modulo = models.CharField(max_length=15, choices=Modulo.choices)
    descripcion = models.TextField(blank=True, default="")
    filtros_disponibles = ArrayField(
        models.CharField(max_length=30), default=list, blank=True,
        help_text="Ej.: fecha_desde, fecha_hasta, empleado, vendedor, periodo.",
    )
    roles_permitidos = ArrayField(models.CharField(max_length=20, choices=Rol.choices), default=list)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "tipo de reporte"
        verbose_name_plural = "tipos de reporte"
        ordering = ["modulo", "nombre"]
        constraints = [
            models.CheckConstraint(
                condition=Q(roles_permitidos__contained_by=Rol.values) & Q(roles_permitidos__len__gt=0),
                name="reporte_roles_validos",
            ),
        ]

    def __str__(self):
        return self.nombre


class ReporteGenerado(models.Model):
    class Formato(models.TextChoices):
        PDF = "PDF", "PDF"
        XLSX = "XLSX", "Excel"
        CSV = "CSV", "CSV"

    tipo = models.ForeignKey(TipoReporte, on_delete=models.PROTECT, related_name="generados")
    generado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    filtros = models.JSONField(default=dict, blank=True)
    formato = models.CharField(max_length=4, choices=Formato.choices, default=Formato.PDF)
    archivo = models.FileField(upload_to="reportes/%Y/%m/", max_length=255, blank=True)
    tamano_bytes = models.PositiveIntegerField(null=True, blank=True)
    generado_en = models.DateTimeField(db_default=Now())

    class Meta:
        verbose_name = "reporte generado"
        verbose_name_plural = "reportes generados"
        ordering = ["-generado_en"]
        indexes = [models.Index(fields=["tipo", "-generado_en"], name="reporte_tipo_fecha_idx")]
