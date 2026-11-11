from decimal import Decimal

from django import forms

from apps.core.forms import FormularioEstilizado, _estilizar
from apps.ventas.forms import sin_numero_de_tarjeta

from . import servicios
from .models import GestionCierre

_MOTIVOS = [("", "Elija el motivo…")] + [(m, m) for m in servicios.MOTIVOS_NO_CONCRETADA]


class ResultadoForm(FormularioEstilizado, forms.Form):
    """SCRUM-52: venta cerrada (con valor) o no concretada (con motivo)."""

    resultado = forms.ChoiceField(label="Resultado", choices=GestionCierre.Resultado.choices, widget=forms.RadioSelect)
    valor_venta = forms.DecimalField(label="Valor de la venta ($)", required=False, min_value=Decimal("0.01"),
                                     max_digits=12, decimal_places=2,
                                     widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))
    motivo = forms.ChoiceField(label="Motivo", required=False, choices=_MOTIVOS)
    observaciones = forms.CharField(label="Observación", required=False, max_length=1000,
                                    widget=forms.Textarea(attrs={"rows": 2}))
    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)
        self.fields["resultado"].widget.attrs["class"] = "form-check-input"

    def clean_observaciones(self):
        return sin_numero_de_tarjeta(self.cleaned_data["observaciones"])

    def clean(self):
        datos = super().clean()
        resultado = datos.get("resultado")
        if resultado == GestionCierre.Resultado.VENTA_CERRADA and not datos.get("valor_venta"):
            self.add_error("valor_venta", "Indique el valor de la venta (mayor a cero).")
        if resultado == GestionCierre.Resultado.NO_CONCRETADA:
            datos["valor_venta"] = None
            if not datos.get("motivo"):
                self.add_error("motivo", "Indique por qué no se concretó la venta.")
        return datos


class CorreccionForm(ResultadoForm):
    """Solo administrador: corrige un resultado ya registrado e indica el motivo."""

    motivo_correccion = forms.CharField(label="Motivo de la corrección", max_length=500,
                                        widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields["observaciones"]
        del self.fields["version"]
        self.fields["motivo_correccion"].widget.attrs["class"] = "form-control"


class ReasignarForm(forms.Form):
    encargado = forms.ModelChoiceField(label="Supervisor", queryset=servicios.encargados_activos(), empty_label="Elija…")
    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["encargado"].queryset = servicios.encargados_activos()
        _estilizar(self)
