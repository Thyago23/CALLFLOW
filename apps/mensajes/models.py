"""
Mensajería interna entre TI, administradores y supervisores (los asesores de ventas no tienen acceso).

Conversaciones directas (dos personas) o grupos con nombre. Quien escribió un mensaje puede corregir su texto
(queda «Editado» y la base guarda cada versión anterior en MensajeEdicion) o eliminarlo («mensaje eliminado»).
Las reglas las garantiza la base de datos (migraciones mensajes/0002 y 0004).
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now

from apps.core.models import ModeloBase

TIPOS_ADJUNTO = ["application/pdf", "image/jpeg", "image/png"]


def ruta_adjunto(instancia, nombre):
    """Nombre aleatorio: la ruta no expone el nombre del archivo ni de quién es."""
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else "bin"
    return f"mensajes/{instancia.conversacion_id}/{uuid.uuid4().hex}.{extension}"


class Conversacion(ModeloBase):
    class Tipo(models.TextChoices):
        DIRECTA = "DIRECTA", "Mensaje directo"
        GRUPO = "GRUPO", "Grupo"

    tipo = models.CharField(max_length=8, choices=Tipo.choices)
    nombre = models.CharField(max_length=80, blank=True, default="")
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    # Directas: «idmenor-idmayor», única: entre dos personas hay una sola conversación.
    clave_directa = models.CharField(max_length=40, null=True, blank=True, unique=True)
    ultimo_mensaje_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "conversación"
        verbose_name_plural = "conversaciones"
        indexes = [models.Index(fields=["-ultimo_mensaje_en"], name="conversacion_actividad_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(tipo__in=["DIRECTA", "GRUPO"]), name="conversacion_tipo_valido"),
            models.CheckConstraint(condition=Q(tipo="DIRECTA", clave_directa__isnull=False) | Q(tipo="GRUPO", clave_directa__isnull=True),
                                   name="conversacion_clave_solo_directas"),
            models.CheckConstraint(condition=Q(tipo="DIRECTA") | ~Q(nombre=""), name="conversacion_grupo_con_nombre"),
        ]

    def __str__(self):
        return self.nombre or f"Conversación {self.pk}"

    @property
    def es_grupo(self) -> bool:
        return self.tipo == self.Tipo.GRUPO


class Participante(models.Model):
    conversacion = models.ForeignKey(Conversacion, on_delete=models.CASCADE, related_name="participantes")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversaciones")
    unido_en = models.DateTimeField(db_default=Now())
    ultimo_leido_en = models.DateTimeField(null=True, blank=True)
    activo = models.BooleanField(default=True, help_text="Falso si salió del grupo (conserva lo que ya se habló).")
    escribiendo_hasta = models.DateTimeField(null=True, blank=True, help_text="Mientras sea futuro, se muestra «escribiendo…».")

    class Meta:
        verbose_name = "participante"
        verbose_name_plural = "participantes"
        constraints = [models.UniqueConstraint(fields=["conversacion", "usuario"], name="mensajes_participante_unico")]


class Mensaje(models.Model):
    conversacion = models.ForeignKey(Conversacion, on_delete=models.CASCADE, related_name="mensajes")
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    texto = models.TextField(blank=True, default="", max_length=4000)
    adjunto = models.FileField(upload_to=ruta_adjunto, blank=True, default="")
    adjunto_nombre = models.CharField(max_length=255, blank=True, default="")
    adjunto_tipo = models.CharField(max_length=50, blank=True, default="")
    creado_en = models.DateTimeField(db_default=Now())
    eliminado = models.BooleanField(default=False)
    eliminado_en = models.DateTimeField(null=True, blank=True)
    editado_en = models.DateTimeField(null=True, blank=True, help_text="Última vez que su autor corrigió el texto.")

    class Meta:
        verbose_name = "mensaje"
        verbose_name_plural = "mensajes"
        ordering = ["creado_en", "pk"]
        indexes = [models.Index(fields=["conversacion", "creado_en"], name="mensaje_conversacion_fecha_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(eliminado=True) | ~Q(texto="") | ~Q(adjunto=""), name="mensaje_con_contenido"),
            models.CheckConstraint(condition=Q(adjunto="") | Q(adjunto_tipo__in=TIPOS_ADJUNTO), name="mensaje_adjunto_tipo_valido"),
            models.CheckConstraint(condition=Q(eliminado=False) | Q(eliminado_en__isnull=False, texto="", adjunto=""),
                                   name="mensaje_eliminado_sin_contenido"),
        ]

    @property
    def es_imagen(self) -> bool:
        return self.adjunto_tipo.startswith("image/")


class MensajeEdicion(models.Model):
    """Versión anterior de un mensaje editado. La escribe la base de datos (trigger), no la aplicación."""

    mensaje = models.ForeignKey(Mensaje, on_delete=models.CASCADE, related_name="ediciones")
    texto_anterior = models.TextField()
    editado_en = models.DateTimeField(db_default=Now())

    class Meta:
        verbose_name = "edición de mensaje"
        verbose_name_plural = "ediciones de mensajes"
        ordering = ["editado_en", "pk"]

