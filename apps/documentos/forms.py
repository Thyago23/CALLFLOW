from django import forms
from django.core.exceptions import ValidationError

from . import contratos
from .models import PlantillaContrato

TIPOS_CONTRATO = [
    ("", "Cualquier tipo de contrato"),
    ("INDEFINIDO", "Indefinido"),
    ("PLAZO_FIJO", "Plazo fijo"),
    ("POR_OBRA", "Por obra o servicio"),
    ("PRUEBA", "Periodo de prueba"),
    ("PASANTIA", "Pasantía"),
]
# Tipos de contrato que necesitan fecha de terminación.
CON_PLAZO = {"PLAZO_FIJO", "PRUEBA", "PASANTIA"}


def _estilizar(form):
    for campo in form.fields.values():
        clase = "form-select" if isinstance(campo.widget, forms.Select) else "form-control"
        campo.widget.attrs["class"] = clase


class PlantillaForm(forms.ModelForm):
    class Meta:
        model = PlantillaContrato
        fields = ["nombre", "tipo_contrato", "cargo", "estilo", "contenido"]
        labels = {"nombre": "Nombre de la plantilla", "tipo_contrato": "Se usa para", "contenido": "Texto del contrato",
                  "cargo": "Se propone para el cargo", "estilo": "Estilo del PDF"}
        widgets = {
            "tipo_contrato": forms.Select(choices=TIPOS_CONTRATO),
            "contenido": forms.Textarea(attrs={"rows": 22, "spellcheck": "true", "class": "font-monospace"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cargo"].empty_label = "Cualquier cargo"
        self.fields["estilo"].required = False  # si no se elige, el estilo simple de siempre
        _estilizar(self)
        self.fields["contenido"].widget.attrs["class"] = "form-control font-monospace texto-plantilla"

    def clean_estilo(self):
        return self.cleaned_data.get("estilo") or PlantillaContrato.Estilo.SIMPLE

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        otras = PlantillaContrato.objects.filter(nombre__iexact=nombre, activa=True)
        if self.instance.pk:
            otras = otras.exclude(nombre=self.instance.nombre)
        if otras.exists():
            raise ValidationError("Ya existe una plantilla activa con ese nombre.")
        return nombre

    def clean_contenido(self):
        contenido = self.cleaned_data["contenido"].replace("\r\n", "\n").strip()
        desconocidas = contratos.variables_desconocidas(contenido)
        if desconocidas:
            raise ValidationError(
                "Variables que no existen: %(lista)s. Revise cómo están escritas en la lista de variables.",
                params={"lista": ", ".join("{{" + v + "}}" for v in sorted(desconocidas))},
            )
        if not contratos.variables_en(contenido):
            raise ValidationError("La plantilla no usa ninguna variable: no se llenaría con los datos del empleado.")
        return contenido


class GenerarContratoForm(forms.Form):
    plantilla = forms.ModelChoiceField(PlantillaContrato.objects.none(), label="Plantilla", empty_label=None)
    fecha_inicio = forms.DateField(label="Inicio del contrato", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    fecha_fin = forms.DateField(
        label="Fin del contrato", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Obligatorio en contratos a plazo fijo, de prueba o pasantía.",
    )
    salario = forms.DecimalField(
        label="Salario mensual (USD)", required=False, max_digits=12, decimal_places=2, min_value=0.01,
        widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
    )

    def __init__(self, *args, empleado, **kwargs):
        self.empleado = empleado
        super().__init__(*args, **kwargs)
        plantillas = PlantillaContrato.objects.filter(activa=True).order_by("nombre")
        self.fields["plantilla"].queryset = plantillas
        if not self.is_bound:
            preferida = contratos.plantilla_para(empleado) or plantillas.first()
            self.initial.update({
                "plantilla": preferida, "fecha_inicio": empleado.fecha_ingreso,
                "salario": contratos.salario_vigente(empleado, empleado.fecha_ingreso),
            })
        _estilizar(self)

    def clean(self):
        datos = super().clean()
        inicio, fin = datos.get("fecha_inicio"), datos.get("fecha_fin")
        if inicio and fin and fin < inicio:
            self.add_error("fecha_fin", "El fin del contrato no puede ser anterior al inicio.")
        if self.empleado.tipo_contrato in CON_PLAZO and not fin:
            self.add_error("fecha_fin", f"El contrato es de tipo «{self.empleado.get_tipo_contrato_display()}»: indique la fecha de fin.")
        return datos
