from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.concurrencia import ControlConcurrencia
from apps.core.forms import FormularioEstilizado, _estilizar
from apps.personal.models import Empleado

from .models import Chip, Reposicion, validar_numero


def _solo_digitos(valor: str) -> str:
    return "".join(c for c in (valor or "") if c.isdigit())


class ChipForm(ControlConcurrencia, FormularioEstilizado, forms.ModelForm):
    # Se acepta con espacios o guiones («099 123-4567»); se guardan solo los 10 dígitos.
    numero = forms.CharField(label="Número", max_length=16)

    class Meta:
        model = Chip
        fields = ["numero", "operadora", "iccid", "asignado_a", "estado", "observacion"]
        labels = {"numero": "Número", "asignado_a": "Asignado a", "observacion": "Observación"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asignado_a"].queryset = (Empleado.objects.exclude(estado=Empleado.Estado.DESVINCULADO)
                                              .order_by("apellidos", "nombres"))
        self.fields["asignado_a"].empty_label = "Sin asignar"
        self.fields["asignado_a"].required = False
        _estilizar(self)
        self.fields["numero"].widget.attrs.update({"inputmode": "numeric", "maxlength": 10, "placeholder": "09XXXXXXXX", "autocomplete": "off"})
        if self.instance.pk:
            # El número solo cambia con una reposición (así queda registrado el anterior).
            self.fields["numero"].disabled = True
            self.fields["numero"].help_text = "Para cambiar el número registre una reposición con «Cambio de número»."
            self.fields["estado"].help_text = "«Dado de baja» lo saca de uso; ya no admite reposiciones."
        else:
            del self.fields["estado"]

    def clean_numero(self):
        if self.instance.pk:
            return self.instance.numero
        numero = _solo_digitos(self.cleaned_data.get("numero"))
        validar_numero(numero)
        return numero

    def clean_iccid(self):
        return _solo_digitos(self.cleaned_data.get("iccid"))


class ReposicionForm(FormularioEstilizado, forms.Form):
    fecha = forms.DateField(label="Fecha de la reposición", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    motivo = forms.ChoiceField(label="Motivo", choices=Reposicion.Motivo.choices)
    cambia_numero = forms.BooleanField(label="Le cambiaron el número", required=False)
    numero_nuevo = forms.CharField(label="Número nuevo", required=False, max_length=14,
                                   help_text="Solo si la operadora entregó otro número. Quedará registrado el anterior.")
    iccid_nuevo = forms.CharField(label="Serie del chip nuevo (ICCID)", required=False, max_length=30,
                                  help_text="Opcional. Si no la escribe, se conserva la serie registrada.")
    operadora = forms.ChoiceField(label="Operadora", choices=Chip._meta.get_field("operadora").choices)
    costo = forms.DecimalField(label="Costo ($)", required=False, min_value=0, max_digits=8, decimal_places=2,
                               help_text="Opcional. Lo que se pagó por la reposición.")
    observacion = forms.CharField(label="Observación", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, chip: Chip, **kwargs):
        self.chip = chip
        kwargs.setdefault("initial", {"fecha": timezone.localdate(), "operadora": chip.operadora})
        super().__init__(*args, **kwargs)
        _estilizar(self)
        self.fields["cambia_numero"].widget.attrs["class"] = "form-check-input"
        self.fields["numero_nuevo"].widget.attrs.update({"inputmode": "numeric", "placeholder": "09XXXXXXXX", "autocomplete": "off"})

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha > timezone.localdate():
            raise ValidationError("La fecha no puede ser futura.")
        return fecha

    def clean(self):
        datos = super().clean()
        cambia = datos.get("cambia_numero") or datos.get("motivo") == Reposicion.Motivo.CAMBIO_NUMERO
        numero = _solo_digitos(datos.get("numero_nuevo"))
        if cambia:
            if not numero:
                self.add_error("numero_nuevo", "Escriba el número nuevo que entregó la operadora.")
            else:
                try:
                    validar_numero(numero)
                except ValidationError as error:
                    self.add_error("numero_nuevo", error)
                else:
                    if numero == self.chip.numero:
                        self.add_error("numero_nuevo", "Es el mismo número que ya tiene el chip.")
        datos["numero_nuevo"] = numero if cambia else ""
        datos["iccid_nuevo"] = _solo_digitos(datos.get("iccid_nuevo"))
        return datos
