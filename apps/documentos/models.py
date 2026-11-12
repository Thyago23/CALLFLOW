"""4.1 Módulo administrativo › Gestión documental."""
import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import F, Q

from apps.core.models import ModeloBase

TIPOS_MIME_PERMITIDOS = ["application/pdf", "image/jpeg", "image/png"]
TAMANO_MAXIMO = 10 * 1024 * 1024  # 10 MB, igual que settings.TAMANO_MAXIMO_ARCHIVO


def ruta_documento(instancia, nombre):
    """Guarda con un nombre aleatorio para no exponer datos personales en la ruta del archivo."""
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else "bin"
    carpeta = f"empleados/{instancia.empleado_id}" if instancia.empleado_id else "general"
    return f"documentos/{carpeta}/{uuid.uuid4().hex}.{extension}"


class TipoDocumento(ModeloBase):
    codigo = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=100)
    requerido_ingreso = models.BooleanField(
        default=False, help_text="Documento obligatorio en el expediente de ingreso del empleado."
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "tipo de documento"
        verbose_name_plural = "tipos de documento"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Documento(ModeloBase):
    """Archivo PDF o imagen. Sirve para expedientes, soportes de asistencia y facturas de nómina."""

    empleado = models.ForeignKey(
        "personal.Empleado", on_delete=models.PROTECT, null=True, blank=True, related_name="documentos"
    )
    tipo = models.ForeignKey(TipoDocumento, on_delete=models.PROTECT, related_name="documentos")
    archivo = models.FileField(upload_to=ruta_documento, max_length=255)
    nombre_original = models.CharField(max_length=255)
    tipo_mime = models.CharField(max_length=50)
    tamano_bytes = models.PositiveIntegerField()
    hash_sha256 = models.CharField(max_length=64, help_text="Huella del archivo para detectar duplicados y alteraciones.")
    descripcion = models.CharField(max_length=255, blank=True, default="")
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    eliminado = models.BooleanField(
        default=False, help_text="Borrado lógico: los documentos se conservan según la política de retención."
    )

    class Meta:
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        ordering = ["-creado_en"]
        indexes = [models.Index(fields=["empleado", "tipo"], name="documento_empleado_tipo_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(tipo_mime__in=TIPOS_MIME_PERMITIDOS), name="documento_tipo_mime_permitido"),
            models.CheckConstraint(
                condition=Q(tamano_bytes__gt=0, tamano_bytes__lte=TAMANO_MAXIMO), name="documento_tamano_valido"
            ),
            models.CheckConstraint(condition=Q(hash_sha256__regex=r"^[0-9a-f]{64}$"), name="documento_hash_valido"),
            models.UniqueConstraint(
                fields=["empleado", "hash_sha256"], condition=Q(eliminado=False), name="documento_sin_duplicados"
            ),
        ]

    def __str__(self):
        return self.nombre_original


class PlantillaContrato(ModeloBase):
    """Plantilla con campos variables, ej.: {{nombres}}, {{identificacion}}, {{cargo}}, {{salario}}."""

    nombre = models.CharField(max_length=100)
    version = models.PositiveIntegerField(default=1)
    tipo_contrato = models.CharField(max_length=12, blank=True, default="")
    contenido = models.TextField()
    campos_variables = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    activa = models.BooleanField(default=True)

    class Estilo(models.TextChoices):
        SIMPLE = "SIMPLE", "Simple (Helvetica, con logo)"
        FORMAL = "FORMAL", "Formal (Times New Roman y títulos en azul, como el contrato de asesores de ventas)"
        OFICIO = "OFICIO", "Oficio (Calibri en negro, como el contrato de imagen corporativa)"

    estilo = models.CharField("estilo del PDF", max_length=10, choices=Estilo.choices, default=Estilo.SIMPLE)
    cargo = models.ForeignKey("personal.Cargo", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                              verbose_name="se propone para el cargo",
                              help_text="Al generar el contrato de alguien con este cargo, se elige esta plantilla.")

    class Meta:
        verbose_name = "plantilla de contrato"
        verbose_name_plural = "plantillas de contrato"
        ordering = ["nombre", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["nombre", "version"], name="plantilla_version_unica"),
            models.UniqueConstraint(fields=["nombre"], condition=Q(activa=True), name="plantilla_una_activa"),
            models.CheckConstraint(condition=Q(version__gte=1), name="plantilla_version_positiva"),
        ]

    def __str__(self):
        return f"{self.nombre} v{self.version}"


class ContratoGenerado(ModeloBase):
    empleado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="contratos")
    plantilla = models.ForeignKey(PlantillaContrato, on_delete=models.PROTECT, related_name="contratos")
    documento = models.OneToOneField(Documento, on_delete=models.PROTECT, related_name="contrato")
    datos_utilizados = models.JSONField(default=dict, help_text="Valores con los que se llenó la plantilla.")
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    generado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    automatico = models.BooleanField(
        "generado al registrar", default=False,
        help_text="Lo generó el sistema al registrar al empleado, con los datos de su CV: el administrador debe revisarlo.",
    )
    revisado_en = models.DateTimeField(null=True, blank=True)
    revisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "contrato generado"
        verbose_name_plural = "contratos generados"
        constraints = [
            models.CheckConstraint(
                condition=Q(fecha_fin__isnull=True) | Q(fecha_fin__gte=F("fecha_inicio")), name="contrato_fechas_validas"
            ),
            models.CheckConstraint(condition=Q(revisado_en__isnull=True) | Q(automatico=True), name="contrato_revision_solo_automaticos"),
        ]

    @property
    def pendiente_revision(self) -> bool:
        return self.automatico and self.revisado_en is None
