from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.core.forms import FormularioEstilizado, _estilizar

from .models import MovimientoCajaChica


class MovimientoCajaForm(FormularioEstilizado, forms.Form):
    """Una fila de la hoja «Caja chica» (el N.° lo pone el sistema)."""
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    tipo = forms.ChoiceField(label="Tipo", choices=[(MovimientoCajaChica.Tipo.EGRESO, "Egreso"),
                                                    (MovimientoCajaChica.Tipo.INGRESO, "Ingreso")])
    categoria = forms.ChoiceField(label="Categoría", choices=MovimientoCajaChica.Categoria.choices)
    concepto = forms.CharField(label="Descripción / motivo", max_length=200,
                               widget=forms.TextInput(attrs={"placeholder": "Ej.: Compra de agua"}))
    comprobante = forms.CharField(label="N.° de comprobante", max_length=50, required=False,
                                  widget=forms.TextInput(attrs={"placeholder": "0"}))
    beneficiario = forms.CharField(label="Beneficiario / proveedor", max_length=150, required=False)
    valor = forms.DecimalField(label="Valor ($)", min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
                               widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))
    forma_pago = forms.ChoiceField(label="Forma de pago", choices=MovimientoCajaChica.FormaPago.choices)

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("initial", {"fecha": timezone.localdate(), "tipo": MovimientoCajaChica.Tipo.EGRESO,
                                      "categoria": MovimientoCajaChica.Categoria.OTROS,
                                      "forma_pago": MovimientoCajaChica.FormaPago.EFECTIVO})
        super().__init__(*args, **kwargs)
        _estilizar(self)


class ArqueoCajaForm(FormularioEstilizado, forms.Form):
    """Cabecera de la hoja «Arqueo de caja». Las cantidades por denominación llegan aparte (cantidad_<denominación>)."""
    fecha = forms.DateField(label="Fecha de arqueo", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    observaciones = forms.CharField(label="Observaciones", required=False, max_length=1000,
                                    widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("initial", {"fecha": timezone.localdate()})
        super().__init__(*args, **kwargs)
        _estilizar(self)


class ReciboCajaForm(FormularioEstilizado, forms.Form):
    """Cabecera del Excel «Recibo Caja Chica» (N.º de recibo y responsable los pone el sistema)."""
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    entregado_a = forms.CharField(label="Entregado a", max_length=150)
    area = forms.CharField(label="Área / Departamento", max_length=100, required=False)
    autorizado_por = forms.CharField(label="Autorizado por", max_length=150, required=False)
    observaciones = forms.CharField(label="Observaciones", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("initial", {"fecha": timezone.localdate()})
        super().__init__(*args, **kwargs)
        _estilizar(self)


class LineaReciboForm(FormularioEstilizado, forms.Form):
    """Una fila de la tabla del recibo."""
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    concepto = forms.CharField(label="Concepto / Detalle del gasto", max_length=200,
                               widget=forms.TextInput(attrs={"placeholder": "Ej.: Entrega para compras / Taxi a notaría"}))
    comprobante = forms.CharField(label="Comprobante", max_length=50, required=False)
    valor_entregado = forms.DecimalField(label="Valor entregado", required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2,
                                         widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal", "placeholder": "0,00"}))
    valor_gastado = forms.DecimalField(label="Valor gastado", required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2,
                                       widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal", "placeholder": "0,00"}))

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("initial", {"fecha": timezone.localdate()})
        super().__init__(*args, **kwargs)
        _estilizar(self)
        for campo in self.fields.values():  # la fila nueva vive dentro de la tabla, como en el Excel
            campo.widget.attrs["form"] = "fila-recibo"
            campo.widget.attrs["class"] += " form-control-sm"

    def clean(self):
        datos = super().clean()
        if not (datos.get("valor_entregado") or datos.get("valor_gastado")):
            raise forms.ValidationError("Escriba el valor entregado o el valor gastado.")
        return datos
