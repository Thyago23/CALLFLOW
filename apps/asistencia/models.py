"""4.1 Módulo administrativo › Asistencia."""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import F, Q

from apps.core.db import sin_solapamiento
from apps.core.models import ModeloBase


def dias_laborables_por_defecto():
    return [1, 2, 3, 4, 5]  # ISO: 1 = lunes … 7 = domingo


class Horario(ModeloBase):
    nombre = models.CharField(max_length=80, unique=True)
    hora_entrada = models.TimeField()
    hora_salida = models.TimeField()
    minutos_tolerancia = models.PositiveSmallIntegerField(default=5)
    dias_laborables = ArrayField(models.PositiveSmallIntegerField(), default=dias_laborables_por_defecto)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "horario"
        verbose_name_plural = "horarios"
        constraints = [
            models.CheckConstraint(condition=Q(hora_salida__gt=F("hora_entrada")), name="horario_salida_posterior"),
            models.CheckConstraint(condition=Q(minutos_tolerancia__lte=120), name="horario_tolerancia_maxima"),
            models.CheckConstraint(
                condition=Q(dias_laborables__contained_by=[1, 2, 3, 4, 5, 6, 7]) & Q(dias_laborables__len__gt=0),
                name="horario_dias_validos",
            ),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.hora_entrada:%H:%M}-{self.hora_salida:%H:%M})"


class AsignacionHorario(ModeloBase):
    empleado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="horarios")
    horario = models.ForeignKey(Horario, on_delete=models.PROTECT, related_name="asignaciones")
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "asignación de horario"
        verbose_name_plural = "asignaciones de horario"
        constraints = [
            models.CheckConstraint(
                condition=Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=F("vigente_desde")),
                name="asig_horario_vigencia_valida",
            ),
            sin_solapamiento("asig_horario_sin_solapamiento", "vigente_desde", "vigente_hasta", "empleado"),
        ]


class RegistroAsistencia(ModeloBase):
    class Origen(models.TextChoices):
        MANUAL = "MANUAL", "Registro manual"
        SISTEMA = "SISTEMA", "Marcación en el sistema"
        IMPORTACION = "IMPORTACION", "Importación"

    empleado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="asistencias")
    fecha = models.DateField()
    entrada = models.DateTimeField()
    salida = models.DateTimeField(null=True, blank=True)
    origen = models.CharField(max_length=12, choices=Origen.choices, default=Origen.SISTEMA)
    observacion = models.CharField(max_length=255, blank=True, default="")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "registro de asistencia"
        verbose_name_plural = "registros de asistencia"
        ordering = ["-fecha", "empleado"]
        indexes = [models.Index(fields=["fecha"], name="asistencia_fecha_idx")]
        constraints = [
            models.UniqueConstraint(fields=["empleado", "fecha"], name="asistencia_una_por_dia"),
            models.CheckConstraint(
                condition=Q(salida__isnull=True) | Q(salida__gt=F("entrada")), name="asistencia_salida_posterior"
            ),
        ]

    def __str__(self):
        return f"{self.empleado} · {self.fecha}"


class NovedadAsistencia(ModeloBase):
    class Tipo(models.TextChoices):
        ATRASO = "ATRASO", "Atraso"
        AUSENCIA = "AUSENCIA", "Ausencia (día completo)"
        AUSENCIA_PARCIAL = "AUSENCIA_PARCIAL", "Ausencia parcial"
        SALIDA_ANTICIPADA = "SALIDA_ANTICIPADA", "Salida anticipada"

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente de revisión"
        JUSTIFICADA = "JUSTIFICADA", "Justificada"
        NO_JUSTIFICADA = "NO_JUSTIFICADA", "No justificada"

    empleado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="novedades_asistencia")
    fecha = models.DateField()
    registro = models.ForeignKey(RegistroAsistencia, on_delete=models.SET_NULL, null=True, blank=True, related_name="novedades")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    minutos = models.PositiveIntegerField(null=True, blank=True)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.PENDIENTE)
    motivo = models.TextField(blank=True, default="")
    soporte = models.ForeignKey(
        "documentos.Documento", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="Certificado médico, permiso, etc.",
    )
    revisada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    revisada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "novedad de asistencia"
        verbose_name_plural = "novedades de asistencia"
        ordering = ["-fecha"]
        indexes = [models.Index(fields=["empleado", "fecha"], name="nov_asistencia_emp_fecha_idx")]
        constraints = [
            models.UniqueConstraint(fields=["empleado", "fecha", "tipo"], name="nov_asistencia_unica"),
            models.CheckConstraint(
                condition=Q(tipo="AUSENCIA") | Q(minutos__isnull=False, minutos__gt=0), name="nov_asistencia_minutos_requeridos"
            ),
            models.CheckConstraint(
                condition=Q(estado="PENDIENTE") | Q(revisada_en__isnull=False), name="nov_asistencia_revision_con_fecha"
            ),
            models.CheckConstraint(
                condition=~Q(estado="JUSTIFICADA") | Q(soporte__isnull=False),
                name="nov_asistencia_justificada_con_soporte",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.empleado} · {self.fecha}"
