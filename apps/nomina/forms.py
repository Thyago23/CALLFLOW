from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.core.forms import FormularioEstilizado, _estilizar

from .models import MovimientoCajaChica


class MovimientoCajaForm(FormularioEstilizado, forms.Form):
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    tipo = forms.ChoiceField(label="Tipo", choices=MovimientoCajaChica.Tipo.choices)
    concepto = forms.CharField(label="Concepto", max_length=200,
                               widget=forms.TextInput(attrs={"placeholder": "Ej.: Compra de agua para la oficina"}))
    beneficiario = forms.CharField(label="Pagado a / entregado por", max_length=150, required=False)
    comprobante = forms.CharField(label="N.° de comprobante", max_length=50, required=False,
                                  widget=forms.TextInput(attrs={"placeholder": "Factura o recibo"}))
    valor = forms.DecimalField(label="Valor ($)", min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
                               widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("initial", {"fecha": timezone.localdate(), "tipo": MovimientoCajaChica.Tipo.EGRESO})
        super().__init__(*args, **kwargs)
        _estilizar(self)
