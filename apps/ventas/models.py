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


class DatosContacto(ModeloBase):
    """Las columnas del formato de la empresa, comunes a los contactos de una base y a los repetidos."""

    CAMPOS = ("fila", "ciudad", "categoria_tarjeta", "direccion", "nombre", "cedula", "cedula_valida", "numero", "numero_alterno")

    fila = models.PositiveIntegerField("fila del archivo", default=0)
    ciudad = models.CharField(max_length=100, blank=True, default="")
    categoria_tarjeta = models.CharField("categoría de tarjeta", max_length=150, blank=True, default="")
    direccion = models.CharField("dirección", max_length=255, blank=True, default="")
    nombre = models.CharField("nombre y apellido", max_length=200, blank=True, default="")
    cedula = models.CharField("cédula", max_length=13, blank=True, default="")
    cedula_valida = models.BooleanField(default=True)
    numero = models.CharField("número", max_length=15)
    numero_alterno = models.CharField("otros números", max_length=60, blank=True, default="")

    class Meta:
        abstract = True

    @property
    def numeros(self) -> list[str]:
        return [self.numero] + [n for n in self.numero_alterno.split(" / ") if n]

    def datos_contacto(self) -> dict:
        return {campo: getattr(self, campo) for campo in self.CAMPOS}


class RegistroBase(DatosContacto):
    """
    Un contacto de una base comercial, con el formato de la empresa: ciudad, categoría de tarjeta,
    dirección, nombre y apellido, cédula y número. Se envía a un vendedor, que lo categoriza.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        NO_CONTESTO = "NO_CONTESTO", "No contestó"
        NO_QUIERE = "NO_QUIERE", "No quiere"
        NO_ENTREGADA = "NO_ENTREGADA", "Tarjeta no entregada"
        # El código se conserva (hay datos y reglas con él); en pantalla es «DAMI efectivo».
        DAMI_SACADO = "DAMI_SACADO", "DAMI efectivo"

    base = models.ForeignKey(BaseComercial, on_delete=models.CASCADE, related_name="registros")
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
            models.CheckConstraint(condition=Q(estado__in=["PENDIENTE", "NO_CONTESTO", "NO_QUIERE", "NO_ENTREGADA", "DAMI_SACADO"]),
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


class ContactoRepetido(DatosContacto):
    """
    «Base de repetidos»: contactos que no entraron a su base porque la misma persona (cédula) o el
    mismo teléfono ya estaba en el mismo archivo o en otra base. Se revisan aquí: se descartan o,
    si de verdad son otra persona, se recuperan a su base.
    """

    class Motivo(models.TextChoices):
        MISMO_ARCHIVO = "MISMO_ARCHIVO", "Repetido en el mismo archivo"
        OTRA_BASE = "OTRA_BASE", "Ya estaba en otra base"

    class Criterio(models.TextChoices):
        CEDULA = "CEDULA", "Misma cédula"
        NUMERO = "NUMERO", "Mismo número"

    class Estado(models.TextChoices):
        POR_REVISAR = "POR_REVISAR", "Por revisar"
        DESCARTADO = "DESCARTADO", "Descartado"
        RECUPERADO = "RECUPERADO", "Recuperado a la base"

    base = models.ForeignKey(BaseComercial, on_delete=models.CASCADE, related_name="repetidos",
                             help_text="Base en la que llegó el contacto repetido.")
    motivo = models.CharField(max_length=13, choices=Motivo.choices)
    criterio = models.CharField("coincide por", max_length=6, choices=Criterio.choices)
    original = models.ForeignKey(RegistroBase, on_delete=models.SET_NULL, null=True, blank=True, related_name="repetidos",
                                 help_text="Contacto que ya existía y con el que coincide.")
    estado = models.CharField(max_length=11, choices=Estado.choices, default=Estado.POR_REVISAR)
    revisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    revisado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "contacto repetido"
        verbose_name_plural = "contactos repetidos"
        ordering = ["-creado_en", "base", "fila", "pk"]
        indexes = [models.Index(fields=["estado", "base"], name="repetido_estado_base_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(motivo__in=["MISMO_ARCHIVO", "OTRA_BASE"]), name="repetido_motivo_valido"),
            models.CheckConstraint(condition=Q(criterio__in=["CEDULA", "NUMERO"]), name="repetido_criterio_valido"),
            models.CheckConstraint(condition=Q(estado__in=["POR_REVISAR", "DESCARTADO", "RECUPERADO"]), name="repetido_estado_valido"),
            models.CheckConstraint(condition=Q(estado="POR_REVISAR") | Q(revisado_en__isnull=False), name="repetido_revisado_con_fecha"),
            models.CheckConstraint(condition=~Q(numero=""), name="repetido_con_numero"),
        ]

    def __str__(self):
        return f"{self.nombre or self.numero} (repetido en {self.base})"


class SolicitudAyuda(ModeloBase):
    """
    Un vendedor le pide a otro asesor que gestione uno de sus contactos. Si acepta, el ayudante lo ve
    como «cliente de» quien pidió la ayuda y puede categorizarlo y llenar el DAMI, que queda a nombre
    de quien pidió la ayuda (su hoja de producción, sus ventas).
    """

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        ACEPTADA = "ACEPTADA", "Aceptada"
        RECHAZADA = "RECHAZADA", "Rechazada"
        CANCELADA = "CANCELADA", "Cancelada"

    ACTIVAS = ("PENDIENTE", "ACEPTADA")

    contacto = models.ForeignKey(RegistroBase, on_delete=models.CASCADE, related_name="ayudas")
    solicitante = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="ayudas_pedidas")
    ayudante = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="ayudas_recibidas")
    mensaje = models.CharField(max_length=500, blank=True, default="")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    respondida_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "solicitud de ayuda"
        verbose_name_plural = "solicitudes de ayuda"
        ordering = ["-creado_en"]
        indexes = [models.Index(fields=["ayudante", "estado"], name="ayuda_ayudante_estado_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(estado__in=["PENDIENTE", "ACEPTADA", "RECHAZADA", "CANCELADA"]), name="ayuda_estado_valido"),
            models.CheckConstraint(condition=~Q(solicitante=F("ayudante")), name="ayuda_no_a_si_mismo"),
            models.CheckConstraint(condition=Q(estado__in=["PENDIENTE", "CANCELADA"]) | Q(respondida_en__isnull=False),
                                   name="ayuda_respondida_con_fecha"),
            # Un contacto tiene a lo sumo una ayuda en curso (pendiente o aceptada).
            models.UniqueConstraint(fields=["contacto"], condition=Q(estado__in=["PENDIENTE", "ACEPTADA"]), name="ayuda_una_activa_por_contacto",
                                    violation_error_message="Ese contacto ya tiene una ayuda pedida."),
        ]

    def __str__(self):
        return f"Ayuda de {self.ayudante} a {self.solicitante} · {self.contacto}"


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

    class Etapa(models.TextChoices):
        """Flujo de cierre: filtro (cierre) → gestión → cierre final (vuelve a quien filtró)."""
        FILTRO = "FILTRO", "Filtro"
        GESTION = "GESTION", "Gestión"
        CIERRE = "CIERRE", "Cierre final"

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
    etapa = models.CharField(
        max_length=8, choices=Etapa.choices, default=Etapa.FILTRO,
        help_text="En qué parte del cierre está: filtro, gestión o cierre final. Al terminar queda la última.",
    )
    encargado_asignado = models.ForeignKey(
        "personal.Empleado", on_delete=models.PROTECT, null=True, blank=True, related_name="formularios_asignados"
    )
    ayudado_por = models.ForeignKey(
        "personal.Empleado", on_delete=models.PROTECT, null=True, blank=True, related_name="formularios_ayudados",
        help_text="Asesor que lo llenó como ayuda. El DAMI y la venta son del vendedor que pidió la ayuda.",
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
            models.CheckConstraint(
                condition=Q(ayudado_por__isnull=True) | ~Q(ayudado_por=F("vendedor")),
                name="dami_ayudante_distinto_vendedor",
            ),
            models.CheckConstraint(condition=Q(etapa__in=["FILTRO", "GESTION", "CIERRE"]), name="dami_etapa_valida"),
            # Recién enviado: está en el filtro y le toca a quien filtra (aún sin persona asignada).
            models.CheckConstraint(
                condition=~Q(estado="ENVIADO") | Q(etapa="FILTRO", encargado_asignado__isnull=True),
                name="dami_enviado_en_filtro",
            ),
            # En gestión siempre hay una persona a cargo de la etapa.
            models.CheckConstraint(
                condition=~Q(estado="EN_GESTION") | Q(encargado_asignado__isnull=False),
                name="dami_en_gestion_con_encargado",
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
