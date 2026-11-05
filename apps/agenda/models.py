"""4.1 Módulo administrativo › Agenda."""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import ModeloBase


class Evento(ModeloBase):
    class Tipo(models.TextChoices):
        ACTIVIDAD = "ACTIVIDAD", "Actividad"
        REUNION = "REUNION", "Reunión"
        RECORDATORIO = "RECORDATORIO", "Recordatorio"

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        COMPLETADO = "COMPLETADO", "Completado"
        CANCELADO = "CANCELADO", "Cancelado"

    titulo = models.CharField(max_length=150)
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.ACTIVIDAD)
    descripcion = models.TextField(blank=True, default="")
    ubicacion = models.CharField(max_length=150, blank=True, default="")
    inicio = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)
    todo_el_dia = models.BooleanField(default=False)
    propietario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="eventos")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    minutos_aviso = models.PositiveIntegerField(default=15, help_text="Anticipación de la notificación.")
    aviso_enviado = models.BooleanField(default=False)
    google_event_id = models.CharField(
        max_length=255, null=True, blank=True, unique=True,
        help_text="Reservado para la posible integración con Google Calendar (SCRUM-30).",
    )

    class Meta:
        verbose_name = "evento"
        verbose_name_plural = "eventos"
        ordering = ["inicio"]
        indexes = [
            models.Index(fields=["propietario", "inicio"], name="evento_propietario_inicio_idx"),
            models.Index(
                fields=["inicio"], condition=Q(estado="PENDIENTE", aviso_enviado=False), name="evento_avisos_pendientes_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(fin__isnull=True) | Q(fin__gte=F("inicio")), name="evento_fin_posterior"),
            models.CheckConstraint(condition=Q(minutos_aviso__lte=10080), name="evento_aviso_maximo_semana"),
        ]

    def __str__(self):
        return self.titulo


class ParticipanteEvento(models.Model):
    evento = models.ForeignKey(Evento, on_delete=models.CASCADE, related_name="participantes")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="eventos_invitado")

    class Meta:
        verbose_name = "participante"
        verbose_name_plural = "participantes"
        constraints = [models.UniqueConstraint(fields=["evento", "usuario"], name="participante_unico")]
