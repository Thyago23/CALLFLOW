"""4.3 Módulo de ventas."""
from django.conf import settings
from django.db import models
from django.db.models import Case, F, Q, When
from django.db.models.functions import Now

from apps.core.models import ModeloBase

DINERO = {"max_digits": 12, "decimal_places": 2}


class EstadoProduccion(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente de cierre"
    VENTA_CERRADA = "VENTA_CERRADA", "Venta cerrada"
    NO_CONCRETADA = "NO_CONCRETADA", "Venta no concretada"
    ANULADO = "ANULADO", "Anulado"


class BaseComercial(ModeloBase):
    nombre = models.CharField(max_length=120, unique=True)
    descripcion = models.TextField(blank=True, default="")
    total_registros = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)
    cargada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "base comercial"
        verbose_name_plural = "bases comerciales"

    def __str__(self):
        return self.nombre


class AsignacionBase(models.Model):
    base = models.ForeignKey(BaseComercial, on_delete=models.PROTECT, related_name="asignaciones")
    vendedor = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="bases_asignadas")
    asignada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    asignada_en = models.DateTimeField(db_default=Now())

    class Meta:
        verbose_name = "asignación de base"
        verbose_name_plural = "asignaciones de base"
        constraints = [models.UniqueConstraint(fields=["base", "vendedor"], name="asignacion_base_unica")]


class SolicitudBase(ModeloBase):
    """Pedido de nuevas bases comerciales que el vendedor envía a los encargados."""

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        ATENDIDA = "ATENDIDA", "Atendida"
        RECHAZADA = "RECHAZADA", "Rechazada"

    vendedor = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="solicitudes_base")
    mensaje = models.TextField(blank=True, default="")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    respuesta = models.TextField(blank=True, default="")
    base_asignada = models.ForeignKey(BaseComercial, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    atendida_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    atendida_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "solicitud de base"
        verbose_name_plural = "solicitudes de base"
        indexes = [models.Index(fields=["estado", "creado_en"], name="solicitud_base_estado_idx")]
        constraints = [
            models.CheckConstraint(
                condition=Q(estado="PENDIENTE") | Q(atendida_en__isnull=False), name="solicitud_base_atendida_con_fecha"
            ),
            models.UniqueConstraint(
                fields=["vendedor"], condition=Q(estado="PENDIENTE"), name="solicitud_base_una_pendiente",
                violation_error_message="Ya tiene una solicitud de base pendiente.",
            ),
        ]


class Cliente(ModeloBase):
    """Cliente único: evita registrar dos veces a la misma persona en distintos formularios."""

    class TipoIdentificacion(models.TextChoices):
        CEDULA = "CEDULA", "Cédula"
        RUC = "RUC", "RUC"
        PASAPORTE = "PASAPORTE", "Pasaporte"

    tipo_identificacion = models.CharField(max_length=10, choices=TipoIdentificacion.choices, default=TipoIdentificacion.CEDULA)
    identificacion = models.CharField(max_length=20)
    nombres = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20)
    telefono_alterno = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    direccion = models.CharField(max_length=255, blank=True, default="")
    ciudad = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        indexes = [models.Index(fields=["apellidos", "nombres"], name="cliente_nombre_idx")]
        constraints = [
            models.UniqueConstraint(fields=["tipo_identificacion", "identificacion"], name="cliente_identificacion_unica"),
            models.CheckConstraint(condition=~Q(identificacion=""), name="cliente_identificacion_no_vacia"),
        ]

    def __str__(self):
        return f"{self.apellidos} {self.nombres}"


class FormularioDAMI(ModeloBase):
    """
    Formulario DAMI digital. Los campos definitivos están pendientes (sección 10 de la
    propuesta), por eso los datos aún no definidos van en `datos_adicionales` (JSONB).

    Por seguridad NO se guardan números de tarjeta: solo si el cliente tiene una tarjeta activa.
    """

    class Estado(models.TextChoices):
        BORRADOR = "BORRADOR", "Borrador"
        ENVIADO = "ENVIADO", "Enviado a cierre"
        EN_GESTION = "EN_GESTION", "En gestión"
        VENTA_CERRADA = "VENTA_CERRADA", "Venta cerrada"
        NO_CONCRETADA = "NO_CONCRETADA", "Venta no concretada"
        ANULADO = "ANULADO", "Anulado"

    vendedor = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="formularios_dami")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="formularios_dami")
    base_comercial = models.ForeignKey(BaseComercial, on_delete=models.SET_NULL, null=True, blank=True, related_name="formularios")
    producto = models.CharField(max_length=120, blank=True, default="")
    tiene_tarjeta_activa = models.BooleanField(default=False)
    prioridad = models.GeneratedField(
        expression=Case(When(tiene_tarjeta_activa=True, then=1), default=2),
        output_field=models.SmallIntegerField(),
        db_persist=True,
        help_text="1 = prioritario (tarjeta activa), 2 = normal. Lo calcula la base de datos.",
    )
    datos_adicionales = models.JSONField(default=dict, blank=True)
    estado = models.CharField(max_length=14, choices=Estado.choices, default=Estado.BORRADOR)
    encargado_asignado = models.ForeignKey(
        "personal.Empleado", on_delete=models.PROTECT, null=True, blank=True, related_name="formularios_asignados"
    )
    enviado_en = models.DateTimeField(null=True, blank=True)
    asignado_en = models.DateTimeField(null=True, blank=True)
    observaciones = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(
        default=1, editable=False,
        help_text="Bloqueo optimista: la base de datos lo incrementa en cada cambio para evitar sobrescrituras.",
    )

    class Meta:
        verbose_name = "formulario DAMI"
        verbose_name_plural = "formularios DAMI"
        ordering = ["prioridad", "enviado_en"]
        indexes = [
            models.Index(
                fields=["prioridad", "enviado_en"], condition=Q(estado__in=["ENVIADO", "EN_GESTION"]), name="dami_cola_cierre_idx"
            ),
            models.Index(fields=["vendedor", "estado"], name="dami_vendedor_estado_idx"),
            models.Index(fields=["encargado_asignado", "estado"], name="dami_encargado_estado_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(estado__in=["BORRADOR", "ENVIADO", "EN_GESTION", "VENTA_CERRADA", "NO_CONCRETADA", "ANULADO"]), name="dami_estado_valido"),
            models.CheckConstraint(
                condition=Q(estado__in=["BORRADOR", "ANULADO"]) | Q(enviado_en__isnull=False), name="dami_enviado_con_fecha"
            ),
            models.CheckConstraint(
                condition=Q(encargado_asignado__isnull=True) | Q(asignado_en__isnull=False), name="dami_asignado_con_fecha"
            ),
            models.CheckConstraint(
                condition=Q(encargado_asignado__isnull=True) | ~Q(encargado_asignado=F("vendedor")),
                name="dami_encargado_distinto_vendedor",
            ),
        ]

    def __str__(self):
        return f"DAMI-{self.pk:06d}" if self.pk else "DAMI (nuevo)"


class HistorialEstadoFormulario(models.Model):
    """Lo llena un trigger cada vez que cambia el estado de un formulario DAMI."""

    formulario = models.ForeignKey(FormularioDAMI, on_delete=models.CASCADE, related_name="historial")
    estado_anterior = models.CharField(max_length=14, blank=True, default="")
    estado_nuevo = models.CharField(max_length=14)
    usuario_id = models.BigIntegerField(null=True, blank=True)
    fecha = models.DateTimeField(db_default=Now())

    class Meta:
        verbose_name = "historial de estado"
        verbose_name_plural = "historial de estados"
        ordering = ["formulario", "fecha"]


class HojaProduccion(ModeloBase):
    """Hoja digital de producción mensual de cada vendedor."""

    vendedor = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="hojas_produccion")
    anio = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField()

    class Meta:
        verbose_name = "hoja de producción"
        verbose_name_plural = "hojas de producción"
        ordering = ["-anio", "-mes"]
        constraints = [
            models.UniqueConstraint(fields=["vendedor", "anio", "mes"], name="hoja_unica_por_mes"),
            models.CheckConstraint(condition=Q(mes__gte=1, mes__lte=12), name="hoja_mes_valido"),
            models.CheckConstraint(condition=Q(anio__gte=2020, anio__lte=2100), name="hoja_anio_valido"),
        ]

    def __str__(self):
        return f"{self.vendedor} · {self.mes:02d}/{self.anio}"


class RegistroProduccion(ModeloBase):
    """Fila de la hoja de producción. Se crea sola al enviar un DAMI y se actualiza al cerrarlo."""

    hoja = models.ForeignKey(HojaProduccion, on_delete=models.PROTECT, related_name="registros")
    formulario = models.OneToOneField(FormularioDAMI, on_delete=models.PROTECT, related_name="registro_produccion")
    fecha = models.DateField()
    estado = models.CharField(max_length=14, choices=EstadoProduccion.choices, default=EstadoProduccion.PENDIENTE)
    valor_venta = models.DecimalField(**DINERO, null=True, blank=True)

    class Meta:
        verbose_name = "registro de producción"
        verbose_name_plural = "registros de producción"
        ordering = ["hoja", "fecha"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(estado="VENTA_CERRADA") & Q(valor_venta__isnull=False, valor_venta__gt=0))
                | (~Q(estado="VENTA_CERRADA") & Q(valor_venta__isnull=True)),
                name="produccion_valor_segun_estado",
            ),
        ]
