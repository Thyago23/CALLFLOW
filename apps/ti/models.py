"""
Departamento de TI: equipos físicos de la empresa, su historial y los equipos conectados a CALLFLOW.

- Equipo: inventario de hardware (computadores, diademas, teléfonos, red, impresoras…), con su
  ficha técnica, datos de red, a quién está asignado, garantía y estado.
- EventoEquipo: hoja de vida del equipo (alta, asignaciones, mantenimientos, reparaciones, baja).
  Es un libro: no se edita ni se borra (lo impide un trigger).
- SesionEquipo: cada inicio de sesión en CALLFLOW con la IP, el navegador y el sistema operativo
  desde el que se conectó. Sirve para ver quién está conectado ahora y desde qué equipo.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import ModeloBase


class TipoEquipo(models.TextChoices):
    ESCRITORIO = "ESCRITORIO", "Computador de escritorio"
    LAPTOP = "LAPTOP", "Laptop"
    MONITOR = "MONITOR", "Monitor"
    DIADEMA = "DIADEMA", "Diadema / auriculares"
    TELEFONO_IP = "TELEFONO_IP", "Teléfono IP"
    CELULAR = "CELULAR", "Celular"
    IMPRESORA = "IMPRESORA", "Impresora"
    RED = "RED", "Equipo de red (router, switch, AP)"
    SERVIDOR = "SERVIDOR", "Servidor"
    UPS = "UPS", "UPS / regulador"
    TV = "TV", "TV / proyector"
    PERIFERICO = "PERIFERICO", "Teclado, mouse u otro periférico"
    OTRO = "OTRO", "Otro"


class EstadoEquipo(models.TextChoices):
    OPERATIVO = "OPERATIVO", "Operativo"
    BODEGA = "BODEGA", "En bodega (disponible)"
    REPARACION = "REPARACION", "En reparación"
    BAJA = "BAJA", "Dado de baja"


class TipoEvento(models.TextChoices):
    ALTA = "ALTA", "Alta en el inventario"
    ASIGNACION = "ASIGNACION", "Asignación"
    DEVOLUCION = "DEVOLUCION", "Devolución"
    MANTENIMIENTO = "MANTENIMIENTO", "Mantenimiento preventivo"
    REPARACION = "REPARACION", "Reparación"
    ACTUALIZACION = "ACTUALIZACION", "Actualización (hardware o software)"
    CAMBIO_ESTADO = "CAMBIO_ESTADO", "Cambio de estado"
    BAJA = "BAJA", "Baja"
    OTRO = "OTRO", "Otro"


class Equipo(ModeloBase):
    Tipo = TipoEquipo
    Estado = EstadoEquipo

    codigo = models.CharField("código de inventario", max_length=20, unique=True,
                              help_text="Si lo deja vacío se asigna uno (EQ-0001…). Conviene pegarlo como etiqueta en el equipo.")
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    marca = models.CharField(max_length=60, blank=True, default="")
    modelo = models.CharField(max_length=80, blank=True, default="")
    numero_serie = models.CharField("número de serie", max_length=80, blank=True, default="")
    # Ficha técnica (computadores y servidores)
    procesador = models.CharField(max_length=80, blank=True, default="")
    memoria_ram_gb = models.PositiveSmallIntegerField("memoria RAM (GB)", null=True, blank=True)
    almacenamiento = models.CharField(max_length=60, blank=True, default="", help_text="Ej.: SSD 512 GB")
    sistema_operativo = models.CharField("sistema operativo", max_length=60, blank=True, default="")
    # Red: la IP permite reconocer el equipo cuando alguien se conecta a CALLFLOW desde él.
    hostname = models.CharField("nombre del equipo en la red", max_length=63, blank=True, default="")
    direccion_ip = models.GenericIPAddressField("dirección IP", null=True, blank=True, unique=True,
                                                help_text="Fija (reservada en el router). Así se reconoce el equipo en «Conectados».")
    direccion_mac = models.CharField("dirección MAC", max_length=17, blank=True, default="")
    # Ubicación y asignación
    ubicacion = models.CharField("ubicación / puesto", max_length=80, blank=True, default="")
    asignado_a = models.ForeignKey("personal.Empleado", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="equipos", verbose_name="asignado a")
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.OPERATIVO)
    # Compra y garantía
    fecha_compra = models.DateField("fecha de compra", null=True, blank=True)
    proveedor = models.CharField(max_length=100, blank=True, default="")
    costo = models.DecimalField("costo ($)", max_digits=10, decimal_places=2, null=True, blank=True)
    garantia_hasta = models.DateField("garantía hasta", null=True, blank=True)
    observacion = models.TextField("observación", blank=True, default="")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "equipo"
        verbose_name_plural = "equipos"
        ordering = ["codigo"]
        constraints = [
            models.CheckConstraint(condition=Q(tipo__in=TipoEquipo.values), name="equipo_tipo_valido"),
            models.CheckConstraint(condition=Q(estado__in=EstadoEquipo.values), name="equipo_estado_valido"),
            models.CheckConstraint(condition=Q(costo__isnull=True) | Q(costo__gte=0), name="equipo_costo_no_negativo"),
            models.CheckConstraint(condition=Q(memoria_ram_gb__isnull=True) | Q(memoria_ram_gb__gt=0), name="equipo_ram_positiva"),
            models.CheckConstraint(condition=Q(garantia_hasta__isnull=True) | Q(fecha_compra__isnull=True)
                                   | Q(garantia_hasta__gte=F("fecha_compra")), name="equipo_garantia_despues_de_compra"),
            # La misma serie no puede estar en dos equipos (las vacías no cuentan).
            models.UniqueConstraint(fields=["numero_serie"], condition=~Q(numero_serie=""), name="equipo_serie_unica"),
        ]

    def __str__(self):
        detalle = " ".join(x for x in (self.marca, self.modelo) if x)
        return f"{self.codigo} · {self.get_tipo_display()}" + (f" {detalle}" if detalle else "")


class EventoEquipo(models.Model):
    """Hoja de vida del equipo. Libro inmutable: los errores se corrigen con un evento nuevo."""

    Tipo = TipoEvento

    equipo = models.ForeignKey(Equipo, on_delete=models.PROTECT, related_name="eventos")
    fecha = models.DateField()
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    descripcion = models.TextField("descripción")
    empleado = models.ForeignKey("personal.Empleado", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                 help_text="Persona a quien se asignó o que devolvió el equipo.")
    realizado_por = models.CharField("realizado por", max_length=100, blank=True, default="",
                                     help_text="Técnico o proveedor que hizo el trabajo.")
    costo = models.DecimalField("costo ($)", max_digits=10, decimal_places=2, null=True, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "evento de equipo"
        verbose_name_plural = "eventos de equipos"
        ordering = ["-fecha", "-pk"]
        indexes = [models.Index(fields=["fecha"], name="evento_equipo_fecha_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(tipo__in=TipoEvento.values), name="evento_equipo_tipo_valido"),
            models.CheckConstraint(condition=Q(costo__isnull=True) | Q(costo__gte=0), name="evento_equipo_costo_no_negativo"),
        ]

    def __str__(self):
        return f"{self.fecha:%d/%m/%Y} · {self.get_tipo_display()} · {self.equipo.codigo}"


class SesionEquipo(models.Model):
    """Un inicio de sesión en CALLFLOW y el equipo (IP, navegador, sistema) desde el que se hizo."""

    class Fin(models.TextChoices):
        CIERRE = "CIERRE", "Cerró sesión"
        CERRADA_TI = "CERRADA_TI", "Cerrada por TI"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sesiones_equipo")
    clave_sesion = models.CharField(max_length=40, unique=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    agente = models.TextField("navegador (texto completo)", blank=True, default="")
    navegador = models.CharField(max_length=40, blank=True, default="")
    sistema = models.CharField("sistema operativo", max_length=40, blank=True, default="")
    dispositivo = models.CharField(max_length=20, blank=True, default="")
    inicio = models.DateTimeField()
    ultima_actividad = models.DateTimeField("última actividad")
    fin = models.DateTimeField(null=True, blank=True)
    motivo_fin = models.CharField("cómo terminó", max_length=12, choices=Fin.choices, blank=True, default="")
    cerrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "sesión de equipo"
        verbose_name_plural = "sesiones de equipos"
        ordering = ["-ultima_actividad"]
        indexes = [models.Index(fields=["fin", "ultima_actividad"], name="sesion_equipo_activa_idx"),
                   models.Index(fields=["inicio"], name="sesion_equipo_inicio_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(fin__isnull=True) | Q(fin__gte=F("inicio")), name="sesion_equipo_fin_valido"),
        ]

    def __str__(self):
        return f"{self.usuario} · {self.ip or 'IP desconocida'} · {self.inicio:%d/%m/%Y %H:%M}"


def ruta_acta_firmada(instancia, nombre):
    """Nombre aleatorio: la ruta no expone a quién se entregó el equipo."""
    import uuid

    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else "pdf"
    return f"ti/actas/{uuid.uuid4().hex}.{extension}"


class ActaEquipo(models.Model):
    """
    Acta de entrega o devolución de un equipo, para imprimir y firmar.

    Es un documento: sus datos no se modifican ni se borran (lo impide un trigger). Lo único que se
    completa después es la copia escaneada del acta firmada.
    """

    class Tipo(models.TextChoices):
        ENTREGA = "ENTREGA", "Entrega"
        DEVOLUCION = "DEVOLUCION", "Devolución"

    class Modalidad(models.TextChoices):
        ASIGNACION = "ASIGNACION", "Asignación (uso permanente)"
        PRESTAMO = "PRESTAMO", "Préstamo (con fecha de devolución)"

    class Condicion(models.TextChoices):
        NUEVO = "NUEVO", "Nuevo"
        BUENO = "BUENO", "Buen estado"
        REGULAR = "REGULAR", "Estado regular (desgaste normal)"
        DANADO = "DANADO", "Con daños"

    equipo = models.ForeignKey(Equipo, on_delete=models.PROTECT, related_name="actas")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    empleado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="actas_equipos",
                                 help_text="Quien recibe el equipo (entrega) o lo devuelve (devolución).")
    fecha = models.DateField()
    modalidad = models.CharField(max_length=10, choices=Modalidad.choices, blank=True, default="",
                                 help_text="Solo en las entregas.")
    devolucion_prevista = models.DateField("devolución prevista", null=True, blank=True,
                                           help_text="Obligatoria en los préstamos.")
    condicion = models.CharField("estado físico", max_length=10, choices=Condicion.choices)
    accesorios = models.CharField(max_length=255, blank=True, default="", help_text="Ej.: cargador, mouse, maletín.")
    observacion = models.TextField("observación", blank=True, default="")
    entrega_acta = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="devoluciones",
                                     help_text="En una devolución, el acta de entrega que cierra.")
    evento = models.OneToOneField(EventoEquipo, on_delete=models.PROTECT, related_name="acta")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    creado_en = models.DateTimeField(auto_now_add=True)
    # Acta firmada (escaneada). Lo único que se completa después de emitirla.
    firmada = models.FileField("acta firmada", upload_to=ruta_acta_firmada, max_length=255, blank=True, default="")
    firmada_mime = models.CharField(max_length=50, blank=True, default="")
    firmada_en = models.DateTimeField(null=True, blank=True)
    firmada_subida_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                           related_name="+")

    class Meta:
        verbose_name = "acta de equipo"
        verbose_name_plural = "actas de equipos"
        ordering = ["-fecha", "-pk"]
        indexes = [models.Index(fields=["fecha"], name="acta_equipo_fecha_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(tipo__in=["ENTREGA", "DEVOLUCION"]), name="acta_tipo_valido"),
            models.CheckConstraint(condition=Q(condicion__in=["NUEVO", "BUENO", "REGULAR", "DANADO"]), name="acta_condicion_valida"),
            # Entrega: con modalidad; préstamo: con fecha de devolución posterior a la entrega.
            models.CheckConstraint(
                condition=Q(tipo="DEVOLUCION", modalidad="", devolucion_prevista__isnull=True)
                | Q(tipo="ENTREGA", modalidad="ASIGNACION", devolucion_prevista__isnull=True)
                | Q(tipo="ENTREGA", modalidad="PRESTAMO", devolucion_prevista__gte=F("fecha")),
                name="acta_modalidad_coherente"),
            models.CheckConstraint(condition=Q(tipo="DEVOLUCION") | Q(entrega_acta__isnull=True), name="acta_entrega_sin_origen"),
            models.CheckConstraint(condition=Q(firmada="") | Q(firmada_en__isnull=False), name="acta_firmada_con_fecha"),
        ]

    def __str__(self):
        return f"{self.numero} · {self.get_tipo_display()} de {self.equipo.codigo}"

    @property
    def numero(self) -> str:
        return f"ACT-{self.pk:05d}" if self.pk else "ACT-(sin emitir)"

    @property
    def es_prestamo(self) -> bool:
        return self.modalidad == self.Modalidad.PRESTAMO
