from decimal import Decimal

from django import forms

from apps.core.forms import FormularioEstilizado, _estilizar

from .models import CategoriaMaterial, Material, MovimientoInventario

_CANTIDAD = {"min_value": Decimal("0.01"), "max_digits": 12, "decimal_places": 2}


class MaterialForm(FormularioEstilizado, forms.ModelForm):
    """SCRUM-25 · Registrar materiales de oficina."""

    categoria_nueva = forms.CharField(label="…o una categoría nueva", required=False, max_length=80,
                                      widget=forms.TextInput(attrs={"placeholder": "Ej.: Insumos de impresión"}))
    stock_inicial = forms.DecimalField(label="Stock inicial", required=False, min_value=Decimal("0"), max_digits=12,
                                       decimal_places=2, help_text="Se registra como una entrada «Stock inicial».")

    class Meta:
        model = Material
        fields = ["codigo", "nombre", "categoria", "unidad_medida", "stock_minimo", "descripcion"]
        labels = {"codigo": "Código", "nombre": "Nombre", "categoria": "Categoría", "unidad_medida": "Unidad",
                  "stock_minimo": "Stock mínimo", "descripcion": "Descripción"}
        help_texts = {"codigo": "Déjelo vacío para generarlo (MAT-0001…).",
                      "stock_minimo": "Al llegar a esta cantidad se avisa a los administradores."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["codigo"].required = False
        self.fields["categoria"].required = False
        self.fields["categoria"].queryset = CategoriaMaterial.objects.order_by("nombre")
        self.fields["categoria"].empty_label = "Seleccione…"
        if self.instance.pk:  # al editar, el stock solo cambia con movimientos
            del self.fields["stock_inicial"]
        _estilizar(self)

    def clean_codigo(self):
        return (self.cleaned_data.get("codigo") or "").strip().upper()

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        repetido = Material.objects.filter(nombre__iexact=nombre).exclude(pk=self.instance.pk).first()
        if repetido:
            raise forms.ValidationError(f"Ya existe el material «{repetido.nombre}» ({repetido.codigo}).")
        return nombre

    def clean(self):
        datos = super().clean()
        if not datos.get("categoria") and not (datos.get("categoria_nueva") or "").strip():
            self.add_error("categoria", "Elija una categoría o escriba una nueva.")
        return datos


class MovimientoForm(FormularioEstilizado, forms.Form):
    """SCRUM-26 · Entradas, salidas y ajustes."""

    material = forms.ModelChoiceField(label="Material", queryset=Material.objects.none(), empty_label="Seleccione…")
    tipo = forms.ChoiceField(label="Tipo de movimiento", choices=MovimientoInventario.Tipo.choices, widget=forms.RadioSelect)
    cantidad = forms.DecimalField(label="Cantidad", **_CANTIDAD)
    motivo = forms.CharField(label="Motivo", required=False, max_length=255,
                             help_text="Obligatorio en los ajustes. Ej.: entrega al área de ventas, compra factura 123.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(activo=True).select_related("categoria").order_by("nombre")
        self.fields["material"].label_from_instance = lambda m: f"{m.nombre} ({m.codigo}) · stock {m.stock_actual.normalize():f}"
        _estilizar(self)
        self.fields["tipo"].widget.attrs["class"] = "btn-check"
