from decimal import Decimal

from django import forms

from apps.core.forms import FormularioEstilizado, _estilizar
from apps.ventas.forms import sin_numero_de_tarjeta

from . import servicios
from .models import GestionCierre

_MOTIVOS = [("", "Elija el motivo…")] + [(m, m) for m in servicios.MOTIVOS_NO_CONCRETADA]
OTRO_MOTIVO = "Otro motivo"
_PREFIJO_OTRO = f"{OTRO_MOTIVO}: "


def inicial_motivo(guardado: str) -> dict:
    """Convierte el motivo guardado ("Otro motivo: texto") en los dos campos del formulario."""
    if guardado.startswith(_PREFIJO_OTRO):
        return {"motivo": OTRO_MOTIVO, "motivo_otro": guardado[len(_PREFIJO_OTRO):]}
    return {"motivo": guardado, "motivo_otro": ""}


class ResultadoForm(FormularioEstilizado, forms.Form):
    """SCRUM-52: venta cerrada (con valor) o no concretada (con motivo)."""

    resultado = forms.ChoiceField(label="Resultado", choices=GestionCierre.Resultado.choices, widget=forms.RadioSelect)
    valor_venta = forms.DecimalField(label="Valor de la venta ($)", required=False, min_value=Decimal("0.01"),
                                     max_digits=12, decimal_places=2,
                                     widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))
    motivo = forms.ChoiceField(label="Motivo", required=False, choices=_MOTIVOS)
    motivo_otro = forms.CharField(label="¿Por qué no se concretó?", required=False,
                                  max_length=255 - len(_PREFIJO_OTRO))
    observaciones = forms.CharField(label="Observación", required=False, max_length=1000,
                                    widget=forms.Textarea(attrs={"rows": 2}))
    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, permitidos=None, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)
        self.fields["resultado"].widget.attrs["class"] = "form-check-input"
        if permitidos is not None:  # en el filtro y en la gestión solo se puede descartar la venta
            self.fields["resultado"].choices = [c for c in GestionCierre.Resultado.choices if c[0] in permitidos]
            if len(permitidos) == 1:
                self.initial.setdefault("resultado", permitidos[0])

    def clean_observaciones(self):
        return sin_numero_de_tarjeta(self.cleaned_data["observaciones"])

    def clean_motivo_otro(self):
        return sin_numero_de_tarjeta(self.cleaned_data["motivo_otro"])

    def clean(self):
        datos = super().clean()
        resultado = datos.get("resultado")
        if resultado == GestionCierre.Resultado.VENTA_CERRADA and not datos.get("valor_venta"):
            self.add_error("valor_venta", "Indique el valor de la venta (mayor a cero).")
        if resultado == GestionCierre.Resultado.VENTA_CERRADA:
            datos["motivo"] = ""
        if resultado == GestionCierre.Resultado.NO_CONCRETADA:
            datos["valor_venta"] = None
            if not datos.get("motivo"):
                self.add_error("motivo", "Indique por qué no se concretó la venta.")
            elif datos["motivo"] == OTRO_MOTIVO:
                texto = (datos.get("motivo_otro") or "").strip()
                if not texto:
                    self.add_error("motivo_otro", "Escriba el motivo por el que no se concretó la venta.")
                else:
                    datos["motivo"] = _PREFIJO_OTRO + texto
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


class NotaPaseForm(forms.Form):
    """Pasar el DAMI a la siguiente etapa, con un comentario opcional para quien lo recibe."""

    nota = forms.CharField(label="Comentario para quien lo recibe (opcional)", required=False, max_length=500,
                           widget=forms.Textarea(attrs={"rows": 2}))
    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)

    def clean_nota(self):
        return sin_numero_de_tarjeta(self.cleaned_data["nota"])


class ResponsablesForm(forms.Form):
    """Administrador: quién hace cada etapa del cierre."""

    filtro = forms.ModelChoiceField(label="Filtro y cierre", required=False, queryset=servicios.encargados_activos(),
                                    empty_label="Sin definir (cualquier supervisor filtra)",
                                    help_text="Recibe los DAMI enviados, filtra al cliente y hace el cierre final.")
    gestion = forms.ModelChoiceField(label="Gestión", required=False, queryset=servicios.encargados_activos(),
                                     empty_label="Sin definir",
                                     help_text="Gestiona los que pasaron el filtro y, si es exitoso, los devuelve para el cierre.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.queryset = servicios.encargados_activos()
        _estilizar(self)


class ReasignarForm(forms.Form):
    encargado = forms.ModelChoiceField(label="Supervisor", queryset=servicios.encargados_activos(), empty_label="Elija…")
    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["encargado"].queryset = servicios.encargados_activos()
        _estilizar(self)
