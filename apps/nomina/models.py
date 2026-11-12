"""4.2 Módulo de nómina (incluye las novedades de nómina de 4.1 › Gestión del personal)."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.db import sin_solapamiento
from apps.core.models import ModeloBase

DINERO = {"max_digits": 12, "decimal_places": 2}
DINERO_CERO = Decimal("0.00")


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


class MovimientoCajaChica(ModeloBase):
    """
    Caja chica: fondos que entran (reposiciones) y gastos menores que salen.
    La base de datos impide que el saldo quede negativo y que un movimiento se modifique o se
    borre: los errores se corrigen anulándolo, con un motivo (queda en la auditoría).
    """

    class Tipo(models.TextChoices):
        EGRESO = "EGRESO", "Egreso"
        INGRESO = "INGRESO", "Ingreso"
        FONDO = "FONDO", "Fondo"  # asignación o reposición del fondo: suma al «Fondo inicial» de la hoja, no a ingresos

    class Categoria(models.TextChoices):
        ALIMENTACION = "ALIMENTACION", "Alimentación"
        TRANSPORTE = "TRANSPORTE", "Transporte"
        UTILES = "UTILES", "Útiles de oficina"
        LIMPIEZA = "LIMPIEZA", "Limpieza"
        MANTENIMIENTO = "MANTENIMIENTO", "Mantenimiento"
        MENSAJERIA = "MENSAJERIA", "Mensajería"
        SERVICIOS = "SERVICIOS", "Servicios"
        OTROS = "OTROS", "Otros"

    class FormaPago(models.TextChoices):
        EFECTIVO = "EFECTIVO", "Efectivo"
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"
        TARJETA = "TARJETA", "Tarjeta"
        OTRO = "OTRO", "Otro"

    fecha = models.DateField()
    tipo = models.CharField(max_length=7, choices=Tipo.choices)
    categoria = models.CharField("categoría", max_length=15, choices=Categoria.choices, default=Categoria.OTROS)
    concepto = models.CharField("descripción / motivo", max_length=200)
    beneficiario = models.CharField("pagado a / entregado por", max_length=150, blank=True, default="")
    comprobante = models.CharField("N.° de comprobante", max_length=50, blank=True, default="",
                                   help_text="Factura, nota de venta o recibo.")
    valor = models.DecimalField(**DINERO)
    forma_pago = models.CharField("forma de pago", max_length=15, choices=FormaPago.choices, default=FormaPago.EFECTIVO)
    recibo = models.ForeignKey("ReciboCaja", on_delete=models.PROTECT, null=True, blank=True, related_name="movimientos",
                               help_text="Entrega, devolución o reembolso de un recibo de caja chica.")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    anulado = models.BooleanField(default=False)
    motivo_anulacion = models.CharField(max_length=255, blank=True, default="")
    anulado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    anulado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "movimiento de caja chica"
        verbose_name_plural = "movimientos de caja chica"
        ordering = ["fecha", "pk"]
        indexes = [models.Index(fields=["fecha"], name="caja_fecha_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(tipo__in=["INGRESO", "EGRESO", "FONDO"]), name="caja_tipo_valido"),
            models.CheckConstraint(condition=Q(valor__gt=0), name="caja_valor_positivo"),
            models.CheckConstraint(condition=~Q(concepto=""), name="caja_concepto_obligatorio"),
            models.CheckConstraint(condition=Q(categoria__in=["ALIMENTACION", "TRANSPORTE", "UTILES", "LIMPIEZA", "MANTENIMIENTO",
                                                              "MENSAJERIA", "SERVICIOS", "OTROS"]), name="caja_categoria_valida"),
            models.CheckConstraint(condition=Q(forma_pago__in=["EFECTIVO", "TRANSFERENCIA", "TARJETA", "OTRO"]),
                                   name="caja_forma_pago_valida"),
            models.CheckConstraint(
                condition=Q(anulado=False) | (Q(anulado_en__isnull=False) & ~Q(motivo_anulacion="")),
                name="caja_anulado_con_motivo",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.concepto}"

    @property
    def entra(self) -> bool:
        """Ingresos y fondo suman a la caja; los egresos restan."""
        return self.tipo != self.Tipo.EGRESO

    @property
    def numero_recibo(self) -> str:
        return f"{self.pk:06d}"


# Billetes y monedas de dólar que se cuentan en el arqueo (en el orden de la hoja «Arqueo de caja»).
DENOMINACIONES = ["100", "50", "20", "10", "5", "1", "0.50", "0.25", "0.10", "0.05", "0.01"]


class ArqueoCaja(ModeloBase):
    """
    Arqueo y cierre de caja chica: se cuenta el efectivo por denominación y se compara con el saldo del registro.
    La base de datos calcula el saldo según registro, el total contado y la diferencia (no se pueden escribir a mano)
    y no deja modificar ni borrar un arqueo (ver migración nomina/0006).
    """

    fecha = models.DateField("fecha de arqueo")
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                    verbose_name="responsable de caja")
    conteo = models.JSONField(default=dict, help_text='Cantidad por denominación: {"20": 3, "0.25": 5}.')
    saldo_registro = models.DecimalField("saldo según registro", **DINERO, default=0)
    total_contado = models.DecimalField("total efectivo contado", **DINERO, default=0)
    diferencia = models.DecimalField("diferencia (contado − registro)", max_digits=12, decimal_places=2, default=0)
    observaciones = models.TextField(blank=True, default="")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "arqueo de caja chica"
        verbose_name_plural = "arqueos de caja chica"
        ordering = ["-fecha", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(total_contado__gte=0), name="arqueo_total_no_negativo"),
            models.CheckConstraint(condition=Q(diferencia=F("total_contado") - F("saldo_registro")), name="arqueo_diferencia_cuadra"),
        ]

    def __str__(self):
        return f"Arqueo del {self.fecha:%d/%m/%Y}"

    @property
    def detalle(self) -> list[dict]:
        """[{denominacion, cantidad, subtotal}] en el orden de la hoja, con las que no se contaron en 0."""

        return [{"denominacion": Decimal(d), "cantidad": int(self.conteo.get(d, 0)),
                 "subtotal": Decimal(d) * int(self.conteo.get(d, 0))} for d in DENOMINACIONES]

    @property
    def cuadra(self) -> bool:
        return self.diferencia == 0


class ReciboCaja(ModeloBase):
    """
    Recibo y control de caja chica (formato de la empresa, Excel «Recibo Caja Chica»): se le entrega dinero a una
    persona, ella detalla lo que gastó y el resumen dice cuánto devuelve o cuánto hay que reembolsarle.

    Mueve la caja: cada «valor entregado» sale como egreso; al liquidar, lo devuelto entra como ingreso y el valor
    adicional a reembolsar sale como egreso. Las reglas las cuida la base de datos (migración nomina/0010).
    """

    class Estado(models.TextChoices):
        ABIERTO = "ABIERTO", "Abierto"  # se registran entregas y gastos
        LIQUIDADO = "LIQUIDADO", "Liquidado"  # ya no se agregan filas; puede quedar saldo pendiente de devolución

    fecha = models.DateField()
    entregado_a = models.CharField("entregado a", max_length=150)
    area = models.CharField("área / departamento", max_length=100, blank=True, default="")
    autorizado_por = models.CharField("autorizado por", max_length=150, blank=True, default="")
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                    verbose_name="responsable de caja")
    observaciones = models.TextField(blank=True, default="")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABIERTO)
    valor_devuelto = models.DecimalField("valor devuelto", **DINERO, default=0)
    valor_reembolsado = models.DecimalField("valor reembolsado", **DINERO, default=0)
    liquidado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "recibo de caja chica"
        verbose_name_plural = "recibos de caja chica"
        ordering = ["-fecha", "-pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(entregado_a=""), name="recibo_entregado_a_obligatorio"),
            models.CheckConstraint(condition=Q(estado__in=["ABIERTO", "LIQUIDADO"]), name="recibo_estado_valido"),
            models.CheckConstraint(condition=Q(valor_devuelto__gte=0) & Q(valor_reembolsado__gte=0), name="recibo_valores_no_negativos"),
            models.CheckConstraint(condition=Q(estado="ABIERTO") | Q(liquidado_en__isnull=False), name="recibo_liquidado_con_fecha"),
        ]

    def __str__(self):
        return f"Recibo N.º {self.numero} · {self.entregado_a}"

    @property
    def numero(self) -> str:
        return f"{self.pk:05d}"

    @property
    def abierto(self) -> bool:
        return self.estado == self.Estado.ABIERTO

    # --- resumen y liquidación (las fórmulas del Excel)
    def _lineas_vigentes(self):
        return [l for l in self.lineas.all() if not l.anulada]

    @property
    def total_entregado(self):
        return sum((l.valor_entregado for l in self._lineas_vigentes()), DINERO_CERO)

    @property
    def total_gastado(self):
        return sum((l.valor_gastado for l in self._lineas_vigentes()), DINERO_CERO)

    @property
    def valor_a_devolver(self):
        return max(self.total_entregado - self.total_gastado, DINERO_CERO)

    @property
    def valor_a_reembolsar(self):
        return max(self.total_gastado - self.total_entregado, DINERO_CERO)

    @property
    def saldo_pendiente(self):
        """Saldo pendiente de devolución."""
        return max(self.valor_a_devolver - self.valor_devuelto, DINERO_CERO)

    @property
    def reembolso_pendiente(self):
        return max(self.valor_a_reembolsar - self.valor_reembolsado, DINERO_CERO)


class LineaRecibo(ModeloBase):
    """Una fila de la tabla del recibo: lo entregado y/o lo gastado, con su comprobante."""

    recibo = models.ForeignKey(ReciboCaja, on_delete=models.PROTECT, related_name="lineas")
    fecha = models.DateField()
    concepto = models.CharField("concepto / detalle del gasto", max_length=200)
    comprobante = models.CharField(max_length=50, blank=True, default="")
    valor_entregado = models.DecimalField("valor entregado", **DINERO, default=0)
    valor_gastado = models.DecimalField("valor gastado", **DINERO, default=0)
    movimiento = models.OneToOneField(MovimientoCajaChica, on_delete=models.PROTECT, null=True, blank=True, related_name="linea_recibo",
                                      help_text="Egreso de caja de lo entregado.")
    anulada = models.BooleanField(default=False)

    class Meta:
        verbose_name = "fila de recibo de caja chica"
        verbose_name_plural = "filas de recibos de caja chica"
        ordering = ["fecha", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(valor_entregado__gte=0) & Q(valor_gastado__gte=0), name="linea_recibo_no_negativa"),
            models.CheckConstraint(condition=Q(valor_entregado__gt=0) | Q(valor_gastado__gt=0), name="linea_recibo_con_valor"),
            models.CheckConstraint(condition=~Q(concepto=""), name="linea_recibo_concepto_obligatorio"),
            models.CheckConstraint(condition=Q(valor_entregado=0) | Q(movimiento__isnull=False), name="linea_recibo_entrega_con_egreso"),
        ]

    def __str__(self):
        return self.concepto
