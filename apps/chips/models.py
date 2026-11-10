"""
Registro de chips (líneas celulares del call) y sus reposiciones.

Solo lo usa la persona a quien se le dio el permiso directo `chips.gestionar_chips` (ver acceso.py).
El número de un chip no se edita a mano: cambia únicamente al registrar una reposición con cambio
de número, para que siempre quede constancia del número anterior y del nuevo.
"""
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q

from apps.core.models import ModeloBase

PATRON_NUMERO = r"^09[0-9]{8}$"
validar_numero = RegexValidator(PATRON_NUMERO, "Escriba el número celular de 10 dígitos, empezando por 09 (ej. 0991234567).")


class Operadora(models.TextChoices):
    CLARO = "CLARO", "Claro"
    CNT = "CNT", "CNT"
    TIGO = "TIGO", "Tigo"
    SUPERMAXI = "SUPERMAXI", "Supermaxi"
    OTRA = "OTRA", "Otra"


class Chip(ModeloBase):
    class Estado(models.TextChoices):
        ACTIVO = "ACTIVO", "Activo"
        SUSPENDIDO = "SUSPENDIDO", "Suspendido"
        BAJA = "BAJA", "Dado de baja"

    numero = models.CharField("número", max_length=10, unique=True, validators=[validar_numero])
    operadora = models.CharField(max_length=10, choices=Operadora.choices, default=Operadora.CLARO)
    iccid = models.CharField("serie del chip (ICCID)", max_length=22, blank=True, default="",
                             help_text="Opcional. Es el número largo impreso en el chip.")
    asignado_a = models.ForeignKey("personal.Empleado", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="chips", verbose_name="asignado a")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ACTIVO)
    observacion = models.CharField("observación", max_length=255, blank=True, default="")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "chip"
        verbose_name_plural = "chips"
        ordering = ["numero"]
        permissions = [("gestionar_chips", "Gestionar el registro de chips y reposiciones")]
        constraints = [
            models.CheckConstraint(condition=Q(numero__regex=PATRON_NUMERO), name="chip_numero_valido"),
            models.CheckConstraint(condition=Q(operadora__in=Operadora.values), name="chip_operadora_valida"),
            models.CheckConstraint(condition=Q(estado__in=["ACTIVO", "SUSPENDIDO", "BAJA"]), name="chip_estado_valido"),
        ]

    def __str__(self):
        return f"{self.numero} ({self.get_operadora_display()})"


class Reposicion(models.Model):
    """
    Libro de reposiciones: no se edita ni se borra (lo impide un trigger). Guarda el número y la serie
    que tenía el chip antes y después, así el reporte muestra qué números se cambiaron.
    """

    class Motivo(models.TextChoices):
        PERDIDA = "PERDIDA", "Pérdida"
        ROBO = "ROBO", "Robo"
        DANO = "DANO", "Daño del chip"
        BLOQUEO = "BLOQUEO", "Bloqueo de la operadora"
        CAMBIO_NUMERO = "CAMBIO_NUMERO", "Cambio de número"
        OTRO = "OTRO", "Otro"

    chip = models.ForeignKey(Chip, on_delete=models.PROTECT, related_name="reposiciones")
    fecha = models.DateField()
    motivo = models.CharField(max_length=15, choices=Motivo.choices)
    numero_anterior = models.CharField("número anterior", max_length=10, validators=[validar_numero])
    numero_nuevo = models.CharField("número nuevo", max_length=10, validators=[validar_numero])
    iccid_anterior = models.CharField("serie anterior", max_length=22, blank=True, default="")
    iccid_nuevo = models.CharField("serie nueva", max_length=22, blank=True, default="")
    operadora = models.CharField(max_length=10, choices=Operadora.choices, help_text="Operadora del chip repuesto.")
    costo = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    observacion = models.TextField("observación", blank=True, default="")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "reposición de chip"
        verbose_name_plural = "reposiciones de chips"
        ordering = ["-fecha", "-pk"]
        indexes = [models.Index(fields=["fecha"], name="reposicion_fecha_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(numero_anterior__regex=PATRON_NUMERO) & Q(numero_nuevo__regex=PATRON_NUMERO),
                                   name="reposicion_numeros_validos"),
            models.CheckConstraint(condition=Q(costo__isnull=True) | Q(costo__gte=0), name="reposicion_costo_no_negativo"),
            # Un «cambio de número» sin número distinto no tiene sentido.
            models.CheckConstraint(condition=~Q(motivo="CAMBIO_NUMERO") | ~Q(numero_nuevo=F("numero_anterior")),
                                   name="reposicion_cambio_numero_distinto"),
        ]

    def __str__(self):
        return f"{self.fecha:%d/%m/%Y} · {self.numero_anterior} → {self.numero_nuevo}"

    @property
    def cambio_numero(self) -> bool:
        return self.numero_nuevo != self.numero_anterior
