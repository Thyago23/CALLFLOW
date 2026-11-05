from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now


class ModeloBase(models.Model):
    """Campos de trazabilidad comunes a todas las tablas del sistema."""

    creado_en = models.DateTimeField("creado en", auto_now_add=True)
    actualizado_en = models.DateTimeField("actualizado en", auto_now=True)

    class Meta:
        abstract = True


class Rol(models.TextChoices):
    ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
    VENDEDOR = "VENDEDOR", "Vendedor"
    ENCARGADO_CIERRE = "ENCARGADO_CIERRE", "Encargado de cierre"


class GestorUsuarios(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        from django.utils import timezone

        extra_fields.setdefault("rol", Rol.ADMINISTRADOR)
        extra_fields.setdefault("aprobado_en", timezone.now())
        return super().create_superuser(username, email, password, **extra_fields)


class Usuario(AbstractUser):
    """Usuario del sistema. El rol define a qué módulos tiene acceso (sección 6 de la propuesta)."""

    email = models.EmailField("correo electrónico", unique=True)
    rol = models.CharField(max_length=20, choices=Rol.choices, default=Rol.VENDEDOR)
    aprobado_en = models.DateTimeField(
        "aprobado en", null=True, blank=True,
        help_text="Vacío y cuenta inactiva = solicitud de registro pendiente de aprobación.",
    )
    aprobado_por = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    objects = GestorUsuarios()

    @property
    def pendiente_aprobacion(self) -> bool:
        return not self.is_active and self.aprobado_en is None

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        constraints = [
            models.CheckConstraint(condition=Q(rol__in=Rol.values), name="usuario_rol_valido"),
        ]

    def __str__(self):
        return self.get_full_name() or self.username


class RegistroCambio(models.Model):
    """
    Bitácora de cambios relevantes (salarios, nómina, resultados comerciales).
    La llenan triggers de PostgreSQL, por lo que no se puede omitir desde la aplicación.
    """

    class Accion(models.TextChoices):
        INSERT = "INSERT", "Creación"
        UPDATE = "UPDATE", "Modificación"
        DELETE = "DELETE", "Eliminación"

    tabla = models.CharField(max_length=63, db_index=True)
    registro_id = models.CharField(max_length=64)
    accion = models.CharField(max_length=6, choices=Accion.choices)
    datos_anteriores = models.JSONField(null=True, blank=True)
    datos_nuevos = models.JSONField(null=True, blank=True)
    # Sin FK a propósito: la auditoría nunca debe bloquear una operación de negocio.
    usuario_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    fecha = models.DateTimeField(db_default=Now(), db_index=True)

    class Meta:
        verbose_name = "registro de cambio"
        verbose_name_plural = "registro de cambios"
        ordering = ["-fecha"]
        indexes = [models.Index(fields=["tabla", "registro_id"], name="cambio_tabla_registro_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(accion__in=["INSERT", "UPDATE", "DELETE"]), name="cambio_accion_valida"),
        ]

    def __str__(self):
        return f"{self.accion} {self.tabla}#{self.registro_id}"


class Notificacion(models.Model):
    """Notificaciones internas: stock bajo, agenda, solicitudes de bases, formularios DAMI, nómina."""

    class Tipo(models.TextChoices):
        GENERAL = "GENERAL", "General"
        STOCK_BAJO = "STOCK_BAJO", "Stock bajo"
        AGENDA = "AGENDA", "Agenda"
        SOLICITUD_BASE = "SOLICITUD_BASE", "Solicitud de base comercial"
        FORMULARIO_DAMI = "FORMULARIO_DAMI", "Formulario DAMI"
        NOMINA = "NOMINA", "Nómina"

    destinatario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="notificaciones")
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.GENERAL)
    titulo = models.CharField(max_length=150)
    mensaje = models.TextField(blank=True, default="")
    url = models.CharField(max_length=300, blank=True, default="")
    leida = models.BooleanField(default=False)
    leida_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(db_default=Now())

    class Meta:
        verbose_name = "notificación"
        verbose_name_plural = "notificaciones"
        ordering = ["-creado_en"]
        indexes = [models.Index(fields=["destinatario", "leida"], name="notif_destinatario_leida_idx")]
        constraints = [
            models.CheckConstraint(
                condition=Q(leida=False) | Q(leida_en__isnull=False), name="notif_leida_requiere_fecha"
            ),
        ]

    def __str__(self):
        return self.titulo
