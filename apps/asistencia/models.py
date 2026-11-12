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
    # Almuerzo: botón «Salir a almorzar» (inicio) y el siguiente inicio de sesión (fin).
    almuerzo_inicio = models.DateTimeField(null=True, blank=True)
    almuerzo_fin = models.DateTimeField(null=True, blank=True)
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
            models.CheckConstraint(
                condition=Q(almuerzo_inicio__isnull=True) | Q(almuerzo_inicio__gte=F("entrada")),
                name="asistencia_almuerzo_tras_entrada",
            ),
            models.CheckConstraint(
                condition=Q(almuerzo_fin__isnull=True) | Q(almuerzo_inicio__isnull=False, almuerzo_fin__gt=F("almuerzo_inicio")),
                name="asistencia_almuerzo_regreso_posterior",
            ),
        ]

    @property
    def en_almuerzo(self) -> bool:
        return self.almuerzo_inicio is not None and self.almuerzo_fin is None

    def __str__(self):
        return f"{self.empleado} · {self.fecha}"


# Novedades que no miden minutos (el resto exige minutos > 0).
TIPOS_SIN_MINUTOS = ("AUSENCIA", "SIN_SALIDA")


class NovedadAsistencia(ModeloBase):
    class Tipo(models.TextChoices):
        ATRASO = "ATRASO", "Atraso"
        AUSENCIA = "AUSENCIA", "Ausencia (día completo)"
        AUSENCIA_PARCIAL = "AUSENCIA_PARCIAL", "Ausencia parcial"
        SALIDA_ANTICIPADA = "SALIDA_ANTICIPADA", "Salida anticipada"
        # El empleado no pulsó «Finalizar día»: la jornada quedó abierta, sin hora de salida.
        SIN_SALIDA = "SIN_SALIDA", "Sin marcación de salida"
        # Regresó del almuerzo después de la hora permitida (más la tolerancia).
        EXCESO_ALMUERZO = "EXCESO_ALMUERZO", "Exceso en almuerzo"

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
                condition=Q(tipo__in=TIPOS_SIN_MINUTOS) | Q(minutos__isnull=False, minutos__gt=0),
                name="nov_asistencia_minutos_requeridos",
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


class Descanso(ModeloBase):
    """
    Break que da el coordinador a los vendedores con la jornada abierta. El tiempo corre desde que
    lo envía (`inicio`) hasta `fin`; cada vendedor marca «Volví del break» y queda su regreso.
    """

    duracion_minutos = models.PositiveSmallIntegerField("duración (minutos)")
    mensaje = models.CharField(max_length=150, blank=True, default="")
    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "break"
        verbose_name_plural = "breaks"
        ordering = ["-inicio"]
        permissions = [("gestionar_breaks", "Dar breaks a los vendedores")]
        constraints = [
            models.CheckConstraint(condition=Q(duracion_minutos__gte=1, duracion_minutos__lte=120), name="descanso_duracion_valida"),
            models.CheckConstraint(condition=Q(fin__gt=F("inicio")), name="descanso_fin_posterior"),
        ]

    def __str__(self):
        return f"Break de {self.duracion_minutos} min · {self.inicio:%d/%m/%Y %H:%M}"


class DescansoProgramado(ModeloBase):
    """
    Break programado por el coordinador para una hora específica. `aviso_minutos` antes (10 o 5) se les
    recuerda a los vendedores con la jornada abierta; a la hora, se convierte en un Descanso normal (el
    mismo flujo de «Volví del break»). Si a esa hora ya hay otro break o no hay vendedores, queda omitido.
    """

    class Estado(models.TextChoices):
        PROGRAMADO = "PROGRAMADO", "Programado"
        AVISADO = "AVISADO", "Recordatorio enviado"
        INICIADO = "INICIADO", "Iniciado"
        OMITIDO = "OMITIDO", "No se pudo iniciar"
        CANCELADO = "CANCELADO", "Cancelado"

    ACTIVOS = (Estado.PROGRAMADO, Estado.AVISADO)
    AVISOS = (10, 5)

    programado_para = models.DateTimeField("hora de inicio")
    duracion_minutos = models.PositiveSmallIntegerField("duración (minutos)")
    aviso_minutos = models.PositiveSmallIntegerField("recordar antes (minutos)", default=10)
    mensaje = models.CharField(max_length=150, blank=True, default="")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PROGRAMADO)
    avisado_en = models.DateTimeField(null=True, blank=True)
    avisados = models.PositiveSmallIntegerField(default=0)  # vendedores a los que les llegó el recordatorio
    descanso = models.OneToOneField(Descanso, on_delete=models.PROTECT, null=True, blank=True, related_name="programacion")
    motivo = models.CharField(max_length=200, blank=True, default="")  # por qué se omitió o se canceló
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    cancelado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "break programado"
        verbose_name_plural = "breaks programados"
        ordering = ["programado_para"]
        indexes = [models.Index(fields=["estado", "programado_para"], name="descanso_prog_estado_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(duracion_minutos__gte=1, duracion_minutos__lte=120), name="descanso_prog_duracion_valida"),
            models.CheckConstraint(condition=Q(aviso_minutos__in=[5, 10]), name="descanso_prog_aviso_valido"),
            models.CheckConstraint(condition=Q(estado__in=["PROGRAMADO", "AVISADO", "INICIADO", "OMITIDO", "CANCELADO"]),
                                   name="descanso_prog_estado_valido"),
            # Iniciado ⇔ tiene su break; el recordatorio enviado deja la hora en que salió.
            models.CheckConstraint(condition=Q(estado="INICIADO", descanso__isnull=False) | (~Q(estado="INICIADO") & Q(descanso__isnull=True)),
                                   name="descanso_prog_iniciado_con_break"),
            models.CheckConstraint(condition=~Q(estado="AVISADO") | Q(avisado_en__isnull=False), name="descanso_prog_avisado_con_hora"),
            # Dos breaks pendientes no pueden empezar a la misma hora.
            models.UniqueConstraint(fields=["programado_para"], condition=Q(estado__in=["PROGRAMADO", "AVISADO"]),
                                    name="descanso_prog_hora_unica"),
        ]

    def __str__(self):
        return f"Break programado de {self.duracion_minutos} min · {self.programado_para:%d/%m/%Y %H:%M}"

    @property
    def fin(self):
        from datetime import timedelta

        return self.programado_para + timedelta(minutes=self.duracion_minutos)

    @property
    def avisar_en(self):
        from datetime import timedelta

        return self.programado_para - timedelta(minutes=self.aviso_minutos)

    @property
    def activo(self) -> bool:
        return self.estado in self.ACTIVOS


class RegresoDescanso(models.Model):
    """Un vendedor en un break: cuándo marcó que volvió y con cuántos minutos de retraso."""

    descanso = models.ForeignKey(Descanso, on_delete=models.CASCADE, related_name="regresos")
    empleado = models.ForeignKey("personal.Empleado", on_delete=models.PROTECT, related_name="regresos_descanso")
    regreso_en = models.DateTimeField(null=True, blank=True)
    minutos_tarde = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "regreso de break"
        verbose_name_plural = "regresos de break"
        constraints = [
            models.UniqueConstraint(fields=["descanso", "empleado"], name="regreso_descanso_unico"),
            models.CheckConstraint(condition=Q(regreso_en__isnull=True, minutos_tarde__isnull=True) | Q(regreso_en__isnull=False, minutos_tarde__isnull=False),
                                   name="regreso_descanso_minutos_con_regreso"),
        ]

    def __str__(self):
        return f"{self.empleado} · {self.descanso}"

    @property
    def tarde(self) -> bool:
        return bool(self.minutos_tarde)
