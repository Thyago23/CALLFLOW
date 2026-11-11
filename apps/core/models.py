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
    VENDEDOR = "VENDEDOR", "Asesor de ventas"
    ENCARGADO_CIERRE = "ENCARGADO_CIERRE", "Supervisor"


# Validación de tarjetas: el administrador no entra, aunque sea superusuario (sí gestiona las bases comerciales).
MODELOS_VETADOS_AL_ADMINISTRADOR = {"entidademisora", "rangobin"}


def ruta_foto_perfil(instancia, nombre):
    """Nombre aleatorio: cada foto nueva tiene otra dirección (el navegador no muestra la vieja de caché)."""
    import uuid

    return f"perfiles/{uuid.uuid4().hex}.jpg"


class Tema(models.TextChoices):
    CLARO = "CLARO", "Claro"
    OSCURO = "OSCURO", "Oscuro"


class GestorUsuarios(UserManager):
    @classmethod
    def normalize_email(cls, email):
        # Sin correo = NULL (la columna es única; "" repetido violaría la unicidad).
        return super().normalize_email(email) or None

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("rol", Rol.ADMINISTRADOR)
        return super().create_superuser(username, email or None, password, **extra_fields)


class Usuario(AbstractUser):
    """
    Usuario del sistema. Lo crea un administrador; `username` guarda el código único
    con el que la persona inicia sesión (iniciales + secuencia, ej. LM001).
    El rol define a qué módulos tiene acceso (sección 6 de la propuesta).
    """

    email = models.EmailField("correo electrónico", unique=True, null=True, blank=True)
    rol = models.CharField(max_length=20, choices=Rol.choices, default=Rol.VENDEDOR)
    debe_cambiar_clave = models.BooleanField(
        "debe cambiar la contraseña", default=False,
        help_text="La contraseña es temporal: se exigirá cambiarla en el próximo ingreso.",
    )
    creado_por = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Mi perfil: lo edita cada persona (foto, apodo interno y modo claro/oscuro).
    foto = models.ImageField("foto de perfil", upload_to=ruta_foto_perfil, blank=True, default="")
    apodo = models.CharField("apodo interno", max_length=30, blank=True, default="")
    tema = models.CharField("modo de pantalla", max_length=10, choices=Tema.choices, default=Tema.CLARO)

    objects = GestorUsuarios()

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        constraints = [
            models.CheckConstraint(condition=Q(rol__in=Rol.values), name="usuario_rol_valido"),
            models.CheckConstraint(condition=~Q(email=""), name="usuario_email_vacio_es_nulo"),
            models.CheckConstraint(condition=Q(tema__in=Tema.values), name="usuario_tema_valido"),
        ]

    @property
    def codigo(self) -> str:
        return self.username

    @property
    def nombre_visible(self) -> str:
        """El apodo si lo tiene; si no, el nombre completo (o el usuario)."""
        return self.apodo or self.get_full_name() or self.username

    @property
    def iniciales(self) -> str:
        if self.apodo:
            partes = self.apodo.split()
            return "".join(p[0] for p in partes[:2]).upper()
        return f"{self.first_name[:1] or self.username[:1]}{self.last_name[:1]}".upper()

    @property
    def url_foto(self) -> str:
        if not self.foto:
            return ""
        from django.urls import reverse

        # El nombre del archivo va en la dirección: al cambiar la foto cambia la dirección y no se usa la de caché.
        return f"{reverse('foto_perfil', args=[self.pk])}?v={self.foto.name.rsplit('/', 1)[-1][:12]}"

    def has_perm(self, perm, obj=None):
        # Va aquí y no en los grupos porque a un superusuario Django le concede todo sin consultarlos.
        app, _, codename = perm.partition(".")
        if self.rol == Rol.ADMINISTRADOR and app == "ventas" and codename.partition("_")[2] in MODELOS_VETADOS_AL_ADMINISTRADOR:
            return False
        return super().has_perm(perm, obj)

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower() or None
        super().save(*args, **kwargs)

    def __str__(self):
        nombre = self.get_full_name()
        return f"{nombre} ({self.username})" if nombre else self.username


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
        ASISTENCIA = "ASISTENCIA", "Asistencia"
        CONTACTOS_REPETIDOS = "CONTACTOS_REPETIDOS", "Contactos repetidos"
        AYUDA_CONTACTO = "AYUDA_CONTACTO", "Ayuda con un contacto"

    destinatario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="notificaciones")
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.GENERAL)
    titulo = models.CharField(max_length=150)
    mensaje = models.TextField(blank=True, default="")
    url = models.CharField(max_length=300, blank=True, default="")
    leida = models.BooleanField(default=False)
    leida_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(db_default=Now())
    # db_default: también las que crean los triggers (stock bajo) quedan pendientes de enviar como push.
    push_enviada = models.BooleanField(default=False, db_default=False)

    class Meta:
        verbose_name = "notificación"
        verbose_name_plural = "notificaciones"
        ordering = ["-creado_en"]
        indexes = [
            models.Index(fields=["destinatario", "leida"], name="notif_destinatario_leida_idx"),
            models.Index(fields=["creado_en"], condition=Q(push_enviada=False), name="notif_push_pendiente_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(leida=False) | Q(leida_en__isnull=False), name="notif_leida_requiere_fecha"
            ),
        ]

    # Ícono (Bootstrap Icons) y tono de cada tipo, para el panel, los avisos y la página de notificaciones.
    ESTILOS = {
        Tipo.GENERAL: ("bell", "neutro"),
        Tipo.STOCK_BAJO: ("box-seam", "alerta"),
        Tipo.AGENDA: ("calendar-event", "marca"),
        Tipo.SOLICITUD_BASE: ("database", "marca"),
        Tipo.FORMULARIO_DAMI: ("file-earmark-text", "marca"),
        Tipo.NOMINA: ("cash-coin", "marca"),
        Tipo.ASISTENCIA: ("clock-history", "alerta"),
        Tipo.CONTACTOS_REPETIDOS: ("people", "alerta"),
        Tipo.AYUDA_CONTACTO: ("person-raised-hand", "marca"),
    }

    @property
    def icono(self) -> str:
        return self.ESTILOS.get(self.tipo, ("bell", "neutro"))[0]

    @property
    def tono(self) -> str:
        return self.ESTILOS.get(self.tipo, ("bell", "neutro"))[1]

    def __str__(self):
        return self.titulo


class SuscripcionPush(ModeloBase):
    """
    Un navegador que aceptó recibir notificaciones push de CALLFLOW (Web Push, estándar W3C).
    Queda ligado al último usuario que inició sesión en ese navegador; al cerrar sesión se borra.
    """

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="suscripciones_push")
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    navegador = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "suscripción push"
        verbose_name_plural = "suscripciones push"

    def __str__(self):
        return f"{self.usuario} · {self.navegador[:40]}"

    @property
    def datos(self) -> dict:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}
