from datetime import date

from django import forms
from django.utils import timezone

from apps.core.forms import FormularioEstilizado, _estilizar
from apps.personal.models import Empleado

from .models import Horario, NovedadAsistencia

DIAS = [(1, "Lun"), (2, "Mar"), (3, "Mié"), (4, "Jue"), (5, "Vie"), (6, "Sáb"), (7, "Dom")]


def _empleados_activos():
    return Empleado.objects.filter(estado=Empleado.Estado.ACTIVO).order_by("apellidos", "nombres")


class RegistroManualForm(FormularioEstilizado, forms.Form):
    """Registro o corrección de un día (olvidos de marcación, días pasados)."""

    empleado = forms.ModelChoiceField(label="Empleado", queryset=Empleado.objects.none(), empty_label="Seleccione…")
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    hora_entrada = forms.TimeField(label="Hora de entrada", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    hora_salida = forms.TimeField(label="Hora de salida", required=False,
                                  widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                                  help_text="Déjela vacía si la persona sigue trabajando.")
    observacion = forms.CharField(label="Observación", required=False, max_length=255,
                                  help_text="Ej.: olvidó marcar la entrada.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empleado"].queryset = _empleados_activos()
        if not self.is_bound:
            self.initial.setdefault("fecha", timezone.localdate())
        _estilizar(self)

    def clean_fecha(self):
        valor = self.cleaned_data["fecha"]
        if valor > timezone.localdate():
            raise forms.ValidationError("No se puede registrar asistencia en una fecha futura.")
        return valor

    def clean(self):
        datos = super().clean()
        entrada, salida = datos.get("hora_entrada"), datos.get("hora_salida")
        if entrada and salida and salida <= entrada:
            self.add_error("hora_salida", "Debe ser posterior a la hora de entrada.")
        return datos


class ClasificarNovedadForm(FormularioEstilizado, forms.Form):
    estado = forms.ChoiceField(label="Clasificación", widget=forms.RadioSelect, choices=[
        (NovedadAsistencia.Estado.JUSTIFICADA, "Justificada"),
        (NovedadAsistencia.Estado.NO_JUSTIFICADA, "No justificada"),
    ])
    motivo = forms.CharField(label="Motivo", required=False, max_length=500,
                             widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Ej.: cita médica con certificado"}))
    soporte = forms.FileField(label="Archivo de respaldo (PDF, JPG o PNG)", required=False,
                              widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}))

    def __init__(self, *args, tiene_soporte: bool = False, **kwargs):
        """`tiene_soporte`: la novedad ya tiene un archivo (p. ej. se reabrió para corregirla)."""
        super().__init__(*args, **kwargs)
        self.tiene_soporte = tiene_soporte
        self.fields["motivo"].widget.attrs["class"] = "form-control form-control-sm"
        self.fields["soporte"].widget.attrs["class"] = "form-control form-control-sm"

    def clean(self):
        datos = super().clean()
        if datos.get("estado") == NovedadAsistencia.Estado.JUSTIFICADA:
            if not (datos.get("motivo") or "").strip():
                self.add_error("motivo", "Indique el motivo de la justificación.")
            if not datos.get("soporte") and not self.tiene_soporte:
                self.add_error("soporte", "Para justificar debe adjuntar el archivo de respaldo.")
        return datos


class HorarioForm(FormularioEstilizado, forms.ModelForm):
    dias_laborables = forms.TypedMultipleChoiceField(
        label="Días laborables", choices=DIAS, coerce=int, initial=[1, 2, 3, 4, 5],
        widget=forms.CheckboxSelectMultiple(attrs={"class": "btn-check"}),
    )

    class Meta:
        model = Horario
        fields = ["nombre", "hora_entrada", "hora_salida", "minutos_tolerancia", "dias_laborables"]
        labels = {"nombre": "Nombre", "hora_entrada": "Entrada", "hora_salida": "Salida",
                  "minutos_tolerancia": "Tolerancia (min)"}
        widgets = {
            "hora_entrada": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "hora_salida": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)
        self.fields["dias_laborables"].widget.attrs["class"] = "btn-check"

    def clean(self):
        datos = super().clean()
        entrada, salida = datos.get("hora_entrada"), datos.get("hora_salida")
        if entrada and salida and salida <= entrada:
            self.add_error("hora_salida", "Debe ser posterior a la hora de entrada.")
        if (datos.get("minutos_tolerancia") or 0) > 120:
            self.add_error("minutos_tolerancia", "La tolerancia máxima es de 120 minutos.")
        return datos


class AsignarHorarioForm(FormularioEstilizado, forms.Form):
    horario = forms.ModelChoiceField(label="Horario", queryset=Horario.objects.filter(activo=True), empty_label="Seleccione…")
    empleados = forms.ModelMultipleChoiceField(label="Empleados", queryset=Empleado.objects.none(),
                                               widget=forms.CheckboxSelectMultiple)
    desde = forms.DateField(label="Vigente desde", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empleados"].queryset = Empleado.objects.exclude(estado=Empleado.Estado.DESVINCULADO).order_by("apellidos", "nombres")
        if not self.is_bound:
            self.initial.setdefault("desde", timezone.localdate())
        _estilizar(self)
        self.fields["empleados"].widget.attrs["class"] = "form-check-input"


class HistorialFiltroForm(forms.Form):
    empleado = forms.ModelChoiceField(label="Empleado", required=False, queryset=Empleado.objects.none(), empty_label="Todos")
    desde = forms.DateField(label="Desde", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    hasta = forms.DateField(label="Hasta", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empleado"].queryset = Empleado.objects.order_by("apellidos", "nombres")
        _estilizar(self)

    def rango(self) -> tuple[date, date]:
        hoy = timezone.localdate()
        datos = self.cleaned_data if self.is_valid() else {}
        desde = datos.get("desde") or hoy.replace(day=1)
        hasta = datos.get("hasta") or hoy
        return (desde, hasta) if desde <= hasta else (hasta, desde)
