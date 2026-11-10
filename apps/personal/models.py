"""4.1 Módulo administrativo › Gestión del personal."""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.db import sin_solapamiento
from apps.core.models import ModeloBase

DINERO = {"max_digits": 12, "decimal_places": 2}


class Cargo(ModeloBase):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True, default="")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "cargo"
        verbose_name_plural = "cargos"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Empleado(ModeloBase):
    class TipoIdentificacion(models.TextChoices):
        CEDULA = "CEDULA", "Cédula"
        RUC = "RUC", "RUC"
        PASAPORTE = "PASAPORTE", "Pasaporte"

    class TipoContrato(models.TextChoices):
        INDEFINIDO = "INDEFINIDO", "Indefinido"
        PLAZO_FIJO = "PLAZO_FIJO", "Plazo fijo"
        POR_OBRA = "POR_OBRA", "Por obra o servicio"
        PRUEBA = "PRUEBA", "Periodo de prueba"
        PASANTIA = "PASANTIA", "Pasantía"

    class Estado(models.TextChoices):
        ACTIVO = "ACTIVO", "Activo"
        VACACIONES = "VACACIONES", "Vacaciones"
        LICENCIA = "LICENCIA", "Licencia"
        INACTIVO = "INACTIVO", "Inactivo"
        DESVINCULADO = "DESVINCULADO", "Desvinculado"

    class TipoCuenta(models.TextChoices):
        AHORROS = "AHORROS", "Ahorros"
        CORRIENTE = "CORRIENTE", "Corriente"

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="empleado",
        help_text="Cuenta de acceso al sistema, si la persona usa CALLFLOW.",
    )
    tipo_identificacion = models.CharField(max_length=10, choices=TipoIdentificacion.choices, default=TipoIdentificacion.CEDULA)
    identificacion = models.CharField(max_length=20)
    nombres = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True, default="")
    telefono = models.CharField(max_length=20, blank=True, default="")
    direccion = models.CharField(max_length=255, blank=True, default="")
    cargo = models.ForeignKey(Cargo, on_delete=models.PROTECT, related_name="empleados")
    tipo_contrato = models.CharField(max_length=12, choices=TipoContrato.choices, default=TipoContrato.INDEFINIDO)
    fecha_ingreso = models.DateField()
    fecha_salida = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.ACTIVO)
    banco = models.CharField(max_length=80, blank=True, default="")
    tipo_cuenta = models.CharField(max_length=10, choices=TipoCuenta.choices, blank=True, default="")
    numero_cuenta = models.CharField(max_length=30, blank=True, default="")

    class Meta:
        verbose_name = "empleado"
        verbose_name_plural = "empleados"
        ordering = ["apellidos", "nombres"]
        indexes = [
            models.Index(fields=["apellidos", "nombres"], name="empleado_nombre_idx"),
            models.Index(fields=["estado"], name="empleado_estado_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["tipo_identificacion", "identificacion"], name="empleado_identificacion_unica"),
            models.CheckConstraint(condition=~Q(identificacion=""), name="empleado_identificacion_no_vacia"),
            models.CheckConstraint(
                condition=Q(fecha_salida__isnull=True) | Q(fecha_salida__gte=F("fecha_ingreso")),
                name="empleado_salida_posterior_ingreso",
            ),
            models.CheckConstraint(
                condition=~Q(estado="DESVINCULADO") | Q(fecha_salida__isnull=False),
                name="empleado_desvinculado_con_fecha_salida",
            ),
            models.CheckConstraint(condition=Q(estado__in=["ACTIVO", "VACACIONES", "LICENCIA", "INACTIVO", "DESVINCULADO"]), name="empleado_estado_valido"),
        ]

    def __str__(self):
        return f"{self.apellidos} {self.nombres}"


class HistorialSalario(ModeloBase):
    """Salario base con fecha de vigencia. Nunca se sobrescribe: cada cambio es una fila nueva."""

    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name="salarios")
    salario_base = models.DecimalField(**DINERO)
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True, help_text="Vacío = vigente actualmente.")
    motivo = models.CharField(max_length=200, blank=True, default="")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "historial de salario"
        verbose_name_plural = "historial de salarios"
        ordering = ["empleado", "-vigente_desde"]
        constraints = [
            models.CheckConstraint(condition=Q(salario_base__gt=0), name="salario_positivo"),
            models.CheckConstraint(
                condition=Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=F("vigente_desde")),
                name="salario_vigencia_valida",
            ),
            sin_solapamiento("salario_sin_solapamiento", "vigente_desde", "vigente_hasta", "empleado"),
        ]

    def __str__(self):
        return f"{self.empleado} · {self.salario_base} desde {self.vigente_desde}"


class EsquemaComision(ModeloBase):
    class Tipo(models.TextChoices):
        PORCENTAJE = "PORCENTAJE", "Porcentaje sobre la venta"
        MONTO_FIJO = "MONTO_FIJO", "Monto fijo por venta"
        ESCALONADO = "ESCALONADO", "Escalonado por tramos"

    nombre = models.CharField(max_length=100, unique=True)
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    porcentaje = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    monto_fijo = models.DecimalField(**DINERO, null=True, blank=True)
    descripcion = models.TextField(blank=True, default="")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "esquema de comisión"
        verbose_name_plural = "esquemas de comisión"
        constraints = [
            models.CheckConstraint(
                condition=Q(porcentaje__isnull=True) | Q(porcentaje__gte=0, porcentaje__lte=100),
                name="comision_porcentaje_rango",
            ),
            models.CheckConstraint(condition=Q(monto_fijo__isnull=True) | Q(monto_fijo__gt=0), name="comision_monto_positivo"),
            models.CheckConstraint(
                condition=~Q(tipo="PORCENTAJE") | Q(porcentaje__isnull=False), name="comision_porcentaje_requerido"
            ),
            models.CheckConstraint(
                condition=~Q(tipo="MONTO_FIJO") | Q(monto_fijo__isnull=False), name="comision_monto_requerido"
            ),
        ]

    def __str__(self):
        return self.nombre


class TramoComision(models.Model):
    """Tramos para comisiones escalonadas (ej.: 0–1000 → 3 %, 1000–5000 → 5 %)."""

    esquema = models.ForeignKey(EsquemaComision, on_delete=models.CASCADE, related_name="tramos")
    desde_monto = models.DecimalField(**DINERO)
    hasta_monto = models.DecimalField(**DINERO, null=True, blank=True)
    porcentaje = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        verbose_name = "tramo de comisión"
        verbose_name_plural = "tramos de comisión"
        ordering = ["esquema", "desde_monto"]
        constraints = [
            models.UniqueConstraint(fields=["esquema", "desde_monto"], name="tramo_inicio_unico"),
            models.CheckConstraint(condition=Q(desde_monto__gte=0), name="tramo_desde_no_negativo"),
            models.CheckConstraint(
                condition=Q(hasta_monto__isnull=True) | Q(hasta_monto__gt=F("desde_monto")), name="tramo_rango_valido"
            ),
            models.CheckConstraint(condition=Q(porcentaje__gte=0, porcentaje__lte=100), name="tramo_porcentaje_rango"),
        ]


class AsignacionComision(ModeloBase):
    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name="comisiones")
    esquema = models.ForeignKey(EsquemaComision, on_delete=models.PROTECT, related_name="asignaciones")
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "asignación de comisión"
        verbose_name_plural = "asignaciones de comisión"
        constraints = [
            models.CheckConstraint(
                condition=Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=F("vigente_desde")),
                name="asig_comision_vigencia_valida",
            ),
            sin_solapamiento("asig_comision_sin_solapamiento", "vigente_desde", "vigente_hasta", "empleado"),
        ]


class ObservacionEmpleado(models.Model):
    """
    Notas del administrador sobre el empleado (felicitaciones, llamados de atención, seguimiento).
    Es un historial: las observaciones no se editan ni se borran, y quedan en la auditoría.
    """

    class Tipo(models.TextChoices):
        GENERAL = "GENERAL", "General"
        POSITIVA = "POSITIVA", "Reconocimiento"
        LLAMADO_ATENCION = "LLAMADO_ATENCION", "Llamado de atención"
        SEGUIMIENTO = "SEGUIMIENTO", "Seguimiento"

    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name="observaciones")
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.GENERAL)
    texto = models.TextField()
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    creado_en = models.DateTimeField("creado en", auto_now_add=True)

    class Meta:
        verbose_name = "observación del empleado"
        verbose_name_plural = "observaciones de empleados"
        ordering = ["-creado_en"]
        indexes = [models.Index(fields=["empleado", "-creado_en"], name="observacion_empleado_idx")]
        constraints = [
            models.CheckConstraint(condition=~Q(texto=""), name="observacion_texto_no_vacio"),
            models.CheckConstraint(
                condition=Q(tipo__in=["GENERAL", "POSITIVA", "LLAMADO_ATENCION", "SEGUIMIENTO"]), name="observacion_tipo_valido"
            ),
        ]

    def __str__(self):
        return f"{self.empleado} · {self.get_tipo_display()} · {self.creado_en:%d/%m/%Y}"
