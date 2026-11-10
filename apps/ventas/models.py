"""4.3 Módulo de ventas."""
from django.conf import settings
from django.db import models
from django.db.models import Case, F, Q, When
from django.db.models.functions import Now

from apps.core.models import ModeloBase

from .tarjetas import MARCAS

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
    archivo_origen = models.CharField("archivo de origen", max_length=255, blank=True, default="")
    resumen_carga = models.JSONField("resumen de la carga", default=dict, blank=True)

    class Meta:
        verbose_name = "base comercial"
        verbose_name_plural = "bases comerciales"

    def __str__(self):
        return self.nombre


class RegistroBase(ModeloBase):
    """
    Un contacto de una base comercial, con el formato de la empresa: ciudad, categoría de tarjeta,
    dirección, nombre y apellido, cédula y número. Se envía a un vendedor, que lo categoriza.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        NO_CONTESTO = "NO_CONTESTO", "No contestó"
        NO_QUIERE = "NO_QUIERE", "No quiere"
        DAMI_SACADO = "DAMI_SACADO", "DAMI sacado"

    base = models.ForeignKey(BaseComercial, on_delete=models.CASCADE, related_name="registros")
    fila = models.PositiveIntegerField("fila del archivo", default=0)
    ciudad = models.CharField(max_length=100, blank=True, default="")
    categoria_tarjeta = models.CharField("categoría de tarjeta", max_length=150, blank=True, default="")
    direccion = models.CharField("dirección", max_length=255, blank=True, default="")
    nombre = models.CharField("nombre y apellido", max_length=200, blank=True, default="")
    cedula = models.CharField("cédula", max_length=13, blank=True, default="")
    cedula_valida = models.BooleanField(default=True)
    numero = models.CharField("número", max_length=15)
    numero_alterno = models.CharField("otros números", max_length=60, blank=True, default="")
    vendedor = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, null=True, blank=True, related_name="contactos_base")
    asignado_en = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    gestionado_en = models.DateTimeField(null=True, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "contacto de base"
        verbose_name_plural = "contactos de base"
        ordering = ["base", "fila", "pk"]
        indexes = [
            models.Index(fields=["vendedor", "estado"], name="registro_base_vendedor_idx"),
            models.Index(fields=["base", "estado"], name="registro_base_estado_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(estado__in=["PENDIENTE", "NO_CONTESTO", "NO_QUIERE", "DAMI_SACADO"]),
                                   name="registro_base_estado_valido"),
            # Solo un contacto enviado a un vendedor se puede categorizar.
            models.CheckConstraint(condition=Q(estado="PENDIENTE") | (Q(vendedor__isnull=False) & Q(gestionado_en__isnull=False)),
                                   name="registro_base_gestion_con_vendedor"),
            models.CheckConstraint(condition=~Q(numero=""), name="registro_base_con_numero"),
        ]

    def __str__(self):
        return f"{self.nombre or self.numero} ({self.base})"

    @property
    def gestionado(self) -> bool:
        return self.estado != self.Estado.PENDIENTE


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

    # Identificación principal: la cédula si la hay; si solo hay RUC, el RUC.
    tipo_identificacion = models.CharField(max_length=10, choices=TipoIdentificacion.choices, default=TipoIdentificacion.CEDULA)
    identificacion = models.CharField(max_length=20)
    ruc = models.CharField("RUC", max_length=13, blank=True, default="")
    nombres = models.CharField(max_length=100, blank=True, default="")
    apellidos = models.CharField(max_length=100, blank=True, default="")
    telefono = models.CharField("teléfono", max_length=20)
    telefono_alterno = models.CharField("teléfono 2", max_length=20, blank=True, default="")
    telefono3 = models.CharField("teléfono 3", max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    direccion = models.CharField(max_length=255, blank=True, default="")
    sector = models.CharField(max_length=120, blank=True, default="")
    ciudad = models.CharField(max_length=80, blank=True, default="")
    referencia = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        indexes = [models.Index(fields=["apellidos", "nombres"], name="cliente_nombre_idx")]
        constraints = [
            models.UniqueConstraint(fields=["tipo_identificacion", "identificacion"], name="cliente_identificacion_unica"),
            models.CheckConstraint(condition=~Q(identificacion=""), name="cliente_identificacion_no_vacia"),
        ]

    def __str__(self):
        nombre = f"{self.apellidos} {self.nombres}".strip()
        return nombre or f"{self.get_tipo_identificacion_display()} {self.identificacion}"

    @property
    def telefonos(self) -> list[str]:
        """Los teléfonos registrados, sin vacíos ni repetidos."""
        return list(dict.fromkeys(t.strip() for t in (self.telefono, self.telefono_alterno, self.telefono3) if t.strip()))

    @property
    def cedula(self) -> str:
        return self.identificacion if self.tipo_identificacion == self.TipoIdentificacion.CEDULA else ""

    @property
    def ruc_mostrar(self) -> str:
        return self.ruc or (self.identificacion if self.tipo_identificacion == self.TipoIdentificacion.RUC else "")


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
    """
    Fila de la hoja de producción. Se crea sola al enviar un DAMI y la gestión de cierre
    actualiza su estado y valor. La aprobación la escribe el vendedor en su hoja.
    """

    hoja = models.ForeignKey(HojaProduccion, on_delete=models.PROTECT, related_name="registros")
    formulario = models.OneToOneField(FormularioDAMI, on_delete=models.PROTECT, related_name="registro_produccion")
    fecha = models.DateField()
    estado = models.CharField(max_length=14, choices=EstadoProduccion.choices, default=EstadoProduccion.PENDIENTE)
    valor_venta = models.DecimalField(**DINERO, null=True, blank=True)
    # db_default: los triggers de la base crean la fila sin indicar este campo.
    aprobacion = models.CharField("aprobación", max_length=20, blank=True, default="", db_default="",
                                  help_text="Número de aprobación del cobro. Solo dígitos.")

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
            models.CheckConstraint(condition=Q(aprobacion__regex=r"^[0-9]*$"), name="produccion_aprobacion_numerica"),
        ]


class TarjetaDAMI(ModeloBase):
    """
    Tarjeta para realizar el pago, indicada en el DAMI.

    El número completo se guarda CIFRADO (clave CLAVE_TARJETAS del .env, fuera de la base de datos)
    y las pantallas solo muestran los últimos 4 dígitos. El número completo lo ve únicamente el
    encargado que tiene tomado el DAMI, mientras lo gestiona (cierre.servicios.numero_tarjeta_visible).
    Esta tabla no se audita ni se registra en el panel /admin, para que el número no se copie a otros lugares.
    """

    class Marca(models.TextChoices):
        DINERS = "DINERS", "Diners"
        VISA = "VISA", "Visa"
        MASTERCARD = "MASTERCARD", "Mastercard"
        AMEX = "AMEX", "American Express"

    formulario = models.OneToOneField(FormularioDAMI, on_delete=models.CASCADE, related_name="tarjeta")
    marca = models.CharField(max_length=10, choices=Marca.choices)
    banco = models.CharField(max_length=80)
    caducidad_mes = models.PositiveSmallIntegerField()
    caducidad_anio = models.PositiveSmallIntegerField()
    ultimos4 = models.CharField("últimos 4 dígitos", max_length=4)
    numero_cifrado = models.TextField(editable=False)

    class Meta:
        verbose_name = "tarjeta del DAMI"
        verbose_name_plural = "tarjetas del DAMI"
        default_permissions = ()  # ningún rol recibe permisos sobre esta tabla
        constraints = [
            models.CheckConstraint(condition=Q(marca__in=["DINERS", "VISA", "MASTERCARD", "AMEX"]), name="tarjeta_marca_valida"),
            models.CheckConstraint(condition=Q(caducidad_mes__gte=1, caducidad_mes__lte=12), name="tarjeta_mes_valido"),
            models.CheckConstraint(condition=Q(caducidad_anio__gte=2020, caducidad_anio__lte=2100), name="tarjeta_anio_valido"),
            models.CheckConstraint(condition=Q(ultimos4__regex=r"^[0-9]{4}$"), name="tarjeta_ultimos4_validos"),
            models.CheckConstraint(condition=~Q(numero_cifrado=""), name="tarjeta_numero_cifrado"),
        ]

    def __str__(self):
        return f"{self.get_marca_display()} **** {self.ultimos4}"

    @property
    def caducidad(self) -> str:
        return f"{self.caducidad_mes:02d}/{self.caducidad_anio % 100:02d}"



class EntidadEmisora(ModeloBase):
    """Bancos y cooperativas del Ecuador que emiten tarjetas. Catálogo para el DAMI y la validación."""

    class Tipo(models.TextChoices):
        BANCO = "BANCO", "Banco"
        COOPERATIVA = "COOPERATIVA", "Cooperativa"
        MUTUALISTA = "MUTUALISTA", "Mutualista"

    nombre = models.CharField(max_length=120, unique=True)
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.BANCO)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "entidad emisora"
        verbose_name_plural = "entidades emisoras"
        ordering = ["tipo", "nombre"]
        constraints = [models.CheckConstraint(condition=Q(tipo__in=["BANCO", "COOPERATIVA", "MUTUALISTA"]), name="entidad_tipo_valido")]

    def __str__(self):
        return self.nombre


class RangoBIN(ModeloBase):
    """
    BIN: primeros 6 a 8 dígitos de la tarjeta, que identifican a la entidad que la emitió.
    Se carga desde la lista que entrega el procesador de pagos (no se inventan).
    """

    class TipoTarjeta(models.TextChoices):
        CREDITO = "CREDITO", "Crédito"
        DEBITO = "DEBITO", "Débito"
        PREPAGO = "PREPAGO", "Prepago"

    bin = models.CharField("BIN", max_length=8, unique=True)
    entidad = models.ForeignKey(EntidadEmisora, on_delete=models.PROTECT, related_name="bins")
    marca = models.CharField(max_length=10, choices=[(c, m.nombre) for c, m in MARCAS.items()])
    tipo_tarjeta = models.CharField("tipo", max_length=8, choices=TipoTarjeta.choices, default=TipoTarjeta.CREDITO)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "rango BIN"
        verbose_name_plural = "rangos BIN"
        ordering = ["bin"]
        constraints = [
            models.CheckConstraint(condition=Q(bin__regex=r"^[0-9]{6,8}$"), name="bin_6_a_8_digitos"),
            models.CheckConstraint(condition=Q(tipo_tarjeta__in=["CREDITO", "DEBITO", "PREPAGO"]), name="bin_tipo_valido"),
            models.CheckConstraint(
                condition=Q(marca__in=["VISA", "MASTERCARD", "MAESTRO", "AMEX", "DINERS", "DISCOVER", "JCB", "UNIONPAY"]),
                name="bin_marca_valida",
            ),
        ]

    def __str__(self):
        return f"{self.bin} · {self.entidad}"
