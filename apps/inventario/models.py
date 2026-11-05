"""4.1 Módulo administrativo › Inventario de suministros."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now

from apps.core.models import ModeloBase

CANTIDAD = {"max_digits": 12, "decimal_places": 2}


class CategoriaMaterial(ModeloBase):
    nombre = models.CharField(max_length=80, unique=True)

    class Meta:
        verbose_name = "categoría de material"
        verbose_name_plural = "categorías de material"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Material(ModeloBase):
    class Unidad(models.TextChoices):
        UNIDAD = "UNIDAD", "Unidad"
        CAJA = "CAJA", "Caja"
        PAQUETE = "PAQUETE", "Paquete"
        RESMA = "RESMA", "Resma"
        LITRO = "LITRO", "Litro"
        KILO = "KILO", "Kilogramo"

    codigo = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=120, unique=True)
    categoria = models.ForeignKey(CategoriaMaterial, on_delete=models.PROTECT, related_name="materiales")
    unidad_medida = models.CharField(max_length=10, choices=Unidad.choices, default=Unidad.UNIDAD)
    stock_actual = models.DecimalField(
        **CANTIDAD, default=0, editable=False,
        help_text="Lo actualiza la base de datos con cada movimiento; no se edita directamente.",
    )
    stock_minimo = models.DecimalField(**CANTIDAD, default=0)
    descripcion = models.CharField(max_length=255, blank=True, default="")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "material"
        verbose_name_plural = "materiales"
        ordering = ["nombre"]
        constraints = [
            models.CheckConstraint(condition=Q(stock_actual__gte=0), name="material_stock_no_negativo"),
            models.CheckConstraint(condition=Q(stock_minimo__gte=0), name="material_minimo_no_negativo"),
        ]

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"

    @property
    def stock_bajo(self) -> bool:
        return self.stock_actual <= self.stock_minimo


class MovimientoInventario(models.Model):
    """
    Libro de movimientos: inmutable. Un trigger aplica cada movimiento al stock del material,
    rechaza salidas mayores al stock disponible y notifica a los administradores si el
    material cae a su nivel mínimo. Para corregir un error se registra un ajuste.
    """

    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", "Entrada"
        SALIDA = "SALIDA", "Salida"
        AJUSTE_POSITIVO = "AJUSTE_POSITIVO", "Ajuste positivo"
        AJUSTE_NEGATIVO = "AJUSTE_NEGATIVO", "Ajuste negativo"

    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="movimientos")
    tipo = models.CharField(max_length=16, choices=Tipo.choices)
    cantidad = models.DecimalField(**CANTIDAD)
    stock_resultante = models.DecimalField(**CANTIDAD, null=True, blank=True, editable=False)
    motivo = models.CharField(max_length=255, blank=True, default="")
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    fecha = models.DateTimeField(db_default=Now())

    class Meta:
        verbose_name = "movimiento de inventario"
        verbose_name_plural = "movimientos de inventario"
        ordering = ["-fecha"]
        indexes = [models.Index(fields=["material", "-fecha"], name="movimiento_material_fecha_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(cantidad__gt=0), name="movimiento_cantidad_positiva"),
            models.CheckConstraint(condition=Q(tipo__in=["ENTRADA", "SALIDA", "AJUSTE_POSITIVO", "AJUSTE_NEGATIVO"]), name="movimiento_tipo_valido"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.cantidad} · {self.material}"
