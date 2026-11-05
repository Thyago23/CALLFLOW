"""4.2 Módulo de nómina (incluye las novedades de nómina de 4.1 › Gestión del personal)."""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.db import sin_solapamiento
from apps.core.models import ModeloBase

DINERO = {"max_digits": 12, "decimal_places": 2}


class Naturaleza(models.TextChoices):
    INGRESO = "INGRESO", "Ingreso"
    DEDUCCION = "DEDUCCION", "Deducción"


class ReglaCalculo(ModeloBase):
    """
    Fórmulas y parámetros aprobados por la empresa, versionados. Un periodo guarda
    qué versiones usó, para poder reproducir cualquier cálculo histórico.
    """

    codigo = models.CharField(max_length=40)
    nombre = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    descripcion = models.TextField(blank=True, default="")
    parametros = models.JSONField(default=dict, help_text='Ej.: {"porcentaje": 9.45} o {"tope": 500}.')
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True)
    aprobada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "regla de cálculo"
        verbose_name_plural = "reglas de cálculo"
        ordering = ["codigo", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["codigo", "version"], name="regla_version_unica"),
            models.CheckConstraint(
                condition=Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=F("vigente_desde")),
                name="regla_vigencia_valida",
            ),
            sin_solapamiento("regla_sin_solapamiento", "vigente_desde", "vigente_hasta", "codigo"),
        ]

    def __str__(self):
        return f"{self.codigo} v{self.version}"


class PeriodoNomina(ModeloBase):
    class Estado(models.TextChoices):
        ABIERTO = "ABIERTO", "Abierto"
        EN_REVISION = "EN_REVISION", "En revisión"
        APROBADO = "APROBADO", "Aprobado"
        PAGADO = "PAGADO", "Pagado"
        CERRADO = "CERRADO", "Cerrado"

    nombre = models.CharField(max_length=60)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    fecha_pago = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.ABIERTO)
    reglas = models.ManyToManyField(ReglaCalculo, blank=True, related_name="periodos", help_text="Reglas usadas en el cálculo.")
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    aprobado_en = models.DateTimeField(null=True, blank=True)
    cerrado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "periodo de nómina"
        verbose_name_plural = "periodos de nómina"
        ordering = ["-fecha_inicio"]
        constraints = [
            models.CheckConstraint(condition=Q(fecha_fin__gte=F("fecha_inicio")), name="periodo_fechas_validas"),
            models.CheckConstraint(
                condition=Q(fecha_pago__isnull=True) | Q(fecha_pago__gte=F("fecha_inicio")), name="periodo_pago_valido"
            ),
            models.CheckConstraint(
                condition=Q(estado__in=["ABIERTO", "EN_REVISION"]) | Q(aprobado_en__isnull=False),
                name="periodo_aprobado_con_fecha",
            ),
            models.CheckConstraint(
                condition=~Q(estado="CERRADO") | Q(cerrado_en__isnull=False), name="periodo_cerrado_con_fecha"
            ),
            sin_solapamiento("periodo_sin_solapamiento", "fecha_inicio", "fecha_fin"),
        ]

    def __str__(self):
        return self.nombre


class TipoNovedadNomina(ModeloBase):
    codigo = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=100)
    naturaleza = models.CharField(max_length=10, choices=Naturaleza.choices)
    requiere_aprobacion = models.BooleanField(default=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "tipo de novedad de nómina"
        verbose_name_plural = "tipos de novedad de nómina"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class NovedadNomina(ModeloBase):
    """Bonos, horas extra, descuentos, etc. Solo las APROBADAS entran al cálculo."""

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        APROBADA = "APROBADA", "Aprobada"
        RECHAZADA = "RECHAZADA", "Rechazada"
        ANULADA = "ANULADA", "Anulada"

    empleado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="novedades_nomina")
    periodo = models.ForeignKey(PeriodoNomina, on_delete=models.PROTECT, related_name="novedades")
    tipo = models.ForeignKey(TipoNovedadNomina, on_delete=models.PROTECT, related_name="novedades")
    descripcion = models.CharField(max_length=255, blank=True, default="")
    cantidad = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    valor = models.DecimalField(**DINERO)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    novedad_asistencia = models.ForeignKey(
        "asistencia.NovedadAsistencia", on_delete=models.SET_NULL, null=True, blank=True, related_name="novedades_nomina"
    )
    registrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    aprobada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    aprobada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "novedad de nómina"
        verbose_name_plural = "novedades de nómina"
        indexes = [models.Index(fields=["periodo", "empleado"], name="novedad_periodo_empleado_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(valor__gt=0), name="novedad_valor_positivo"),
            models.CheckConstraint(condition=Q(cantidad__gt=0), name="novedad_cantidad_positiva"),
            models.CheckConstraint(
                condition=~Q(estado="APROBADA") | Q(aprobada_en__isnull=False), name="novedad_aprobada_con_fecha"
            ),
        ]


class LiquidacionNomina(ModeloBase):
    """Resultado del cálculo de nómina de un empleado en un periodo."""

    class Estado(models.TextChoices):
        BORRADOR = "BORRADOR", "Borrador"
        CALCULADA = "CALCULADA", "Calculada"
        APROBADA = "APROBADA", "Aprobada"
        PAGADA = "PAGADA", "Pagada"
        ANULADA = "ANULADA", "Anulada"

    periodo = models.ForeignKey(PeriodoNomina, on_delete=models.PROTECT, related_name="liquidaciones")
    empleado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="liquidaciones")
    salario_base = models.DecimalField(**DINERO, default=0)
    total_comisiones = models.DecimalField(**DINERO, default=0)
    total_ingresos = models.DecimalField(**DINERO, default=0)
    total_deducciones = models.DecimalField(**DINERO, default=0)
    neto = models.DecimalField(**DINERO, default=0)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.BORRADOR)
    calculada_en = models.DateTimeField(null=True, blank=True)
    observaciones = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "liquidación de nómina"
        verbose_name_plural = "liquidaciones de nómina"
        constraints = [
            models.UniqueConstraint(fields=["periodo", "empleado"], name="liquidacion_unica_por_periodo"),
            models.CheckConstraint(
                condition=Q(salario_base__gte=0, total_comisiones__gte=0, total_ingresos__gte=0, total_deducciones__gte=0),
                name="liquidacion_valores_no_negativos",
            ),
            models.CheckConstraint(
                condition=Q(neto=F("total_ingresos") - F("total_deducciones")), name="liquidacion_neto_cuadra"
            ),
            models.CheckConstraint(condition=Q(neto__gte=0), name="liquidacion_neto_no_negativo"),
        ]

    def __str__(self):
        return f"{self.empleado} · {self.periodo}"


class DetalleLiquidacion(models.Model):
    liquidacion = models.ForeignKey(LiquidacionNomina, on_delete=models.CASCADE, related_name="detalles")
    concepto = models.CharField(max_length=150)
    naturaleza = models.CharField(max_length=10, choices=Naturaleza.choices)
    valor = models.DecimalField(**DINERO)
    regla = models.ForeignKey(ReglaCalculo, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    novedad = models.ForeignKey(NovedadNomina, on_delete=models.PROTECT, null=True, blank=True, related_name="detalles")
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "detalle de liquidación"
        verbose_name_plural = "detalles de liquidación"
        ordering = ["liquidacion", "orden"]
        constraints = [
            models.CheckConstraint(condition=Q(valor__gt=0), name="detalle_valor_positivo"),
            models.UniqueConstraint(
                fields=["liquidacion", "novedad"], condition=Q(novedad__isnull=False), name="detalle_novedad_una_vez"
            ),
        ]


class SolicitudPago(ModeloBase):
    """Solicitud de pago de una liquidación. La factura (PDF o imagen) es obligatoria."""

    class Estado(models.TextChoices):
        SOLICITADA = "SOLICITADA", "Solicitada"
        APROBADA = "APROBADA", "Aprobada"
        RECHAZADA = "RECHAZADA", "Rechazada"
        PAGADA = "PAGADA", "Pagada"

    liquidacion = models.OneToOneField(LiquidacionNomina, on_delete=models.PROTECT, related_name="solicitud_pago")
    factura = models.ForeignKey("documentos.Documento", on_delete=models.PROTECT, related_name="solicitudes_pago")
    monto = models.DecimalField(**DINERO)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.SOLICITADA)
    solicitada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    revisada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    revisada_en = models.DateTimeField(null=True, blank=True)
    pagada_en = models.DateTimeField(null=True, blank=True)
    referencia_pago = models.CharField(max_length=100, blank=True, default="")
    observaciones = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "solicitud de pago"
        verbose_name_plural = "solicitudes de pago"
        constraints = [
            models.CheckConstraint(condition=Q(monto__gt=0), name="solicitud_monto_positivo"),
            models.CheckConstraint(
                condition=Q(estado="SOLICITADA") | Q(revisada_en__isnull=False), name="solicitud_revision_con_fecha"
            ),
            models.CheckConstraint(
                condition=~Q(estado="PAGADA") | Q(pagada_en__isnull=False), name="solicitud_pagada_con_fecha"
            ),
        ]
