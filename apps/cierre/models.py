"""4.4 Módulo de cierre de ventas."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now

from apps.core.models import ModeloBase

DINERO = {"max_digits": 12, "decimal_places": 2}


class MetaMensual(ModeloBase):
    vendedor = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="metas")
    anio = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField()
    monto_meta = models.DecimalField(**DINERO)
    definida_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "meta mensual"
        verbose_name_plural = "metas mensuales"
        ordering = ["-anio", "-mes"]
        constraints = [
            models.UniqueConstraint(fields=["vendedor", "anio", "mes"], name="meta_unica_por_mes"),
            models.CheckConstraint(condition=Q(mes__gte=1, mes__lte=12), name="meta_mes_valido"),
            models.CheckConstraint(condition=Q(anio__gte=2020, anio__lte=2100), name="meta_anio_valido"),
            models.CheckConstraint(condition=Q(monto_meta__gt=0), name="meta_monto_positivo"),
        ]


class GestionCierre(ModeloBase):
    """
    Resultado de la gestión de un DAMI. Un trigger valida que el formulario esté en cola
    y que lo gestione el encargado asignado, y luego actualiza el formulario y la hoja de
    producción del vendedor.
    """

    class Resultado(models.TextChoices):
        VENTA_CERRADA = "VENTA_CERRADA", "Venta cerrada"
        NO_CONCRETADA = "NO_CONCRETADA", "Venta no concretada"

    formulario = models.OneToOneField("ventas.FormularioDAMI", on_delete=models.PROTECT, related_name="gestion")
    encargado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="gestiones_cierre")
    resultado = models.CharField(max_length=14, choices=Resultado.choices)
    valor_venta = models.DecimalField(**DINERO, null=True, blank=True)
    motivo_no_concretada = models.CharField(max_length=255, blank=True, default="")
    observaciones = models.TextField(blank=True, default="")
    gestionado_en = models.DateTimeField(db_default=Now())

    class Meta:
        verbose_name = "gestión de cierre"
        verbose_name_plural = "gestiones de cierre"
        indexes = [models.Index(fields=["encargado", "gestionado_en"], name="gestion_encargado_fecha_idx")]
        constraints = [
            models.CheckConstraint(
                condition=(Q(resultado="VENTA_CERRADA") & Q(valor_venta__isnull=False, valor_venta__gt=0))
                | (Q(resultado="NO_CONCRETADA") & Q(valor_venta__isnull=True)),
                name="gestion_valor_segun_resultado",
            ),
        ]


class PreferenciaPanel(models.Model):
    """Permite ocultar temporalmente los valores económicos del panel comercial."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferencia_panel")
    ocultar_valores = models.BooleanField(default=False)

    class Meta:
        verbose_name = "preferencia de panel"
        verbose_name_plural = "preferencias de panel"


class ResumenVendedorMes(models.Model):
    """
    Vista de PostgreSQL (cierre_v_resumen_vendedor_mes): totales por vendedor y mes,
    meta, valor pendiente y posición en el ranking (posicion = 1 → mejor vendedor del mes).
    Solo lectura.
    """

    id = models.CharField(primary_key=True, max_length=40)
    vendedor = models.ForeignKey("personal.Empleado", on_delete=models.DO_NOTHING, related_name="+")
    anio = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField()
    total_formularios = models.IntegerField()
    ventas_cerradas = models.IntegerField()
    ventas_no_concretadas = models.IntegerField()
    pendientes = models.IntegerField()
    total_vendido = models.DecimalField(**DINERO)
    monto_meta = models.DecimalField(**DINERO, null=True)
    pendiente_meta = models.DecimalField(**DINERO, null=True)
    porcentaje_meta = models.DecimalField(max_digits=7, decimal_places=2, null=True)
    posicion = models.IntegerField()

    class Meta:
        managed = False
        db_table = "cierre_v_resumen_vendedor_mes"
        verbose_name = "resumen de vendedor por mes"
        verbose_name_plural = "resumen de vendedores por mes"
        ordering = ["-anio", "-mes", "posicion"]
