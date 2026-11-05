from datetime import date, timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core import validadores

from .models import Cargo, Empleado


class EmpleadoForm(forms.ModelForm):
    """SCRUM-12 · Registrar nuevos empleados: datos personales, de contacto y laborales."""

    class Meta:
        model = Empleado
        fields = [
            "tipo_identificacion", "identificacion", "nombres", "apellidos", "fecha_nacimiento",
            "telefono", "email", "direccion",
            "cargo", "tipo_contrato", "fecha_ingreso",
        ]
        labels = {
            "tipo_identificacion": "Tipo de identificación",
            "identificacion": "Número de identificación",
            "fecha_nacimiento": "Fecha de nacimiento",
            "telefono": "Teléfono",
            "email": "Correo electrónico",
            "direccion": "Dirección",
            "tipo_contrato": "Tipo de contrato",
            "fecha_ingreso": "Fecha de ingreso",
        }
        widgets = {
            "fecha_nacimiento": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "fecha_ingreso": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "identificacion": forms.TextInput(attrs={"autocomplete": "off", "inputmode": "numeric"}),
            "telefono": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "0991234567"}),
            "email": forms.EmailInput(attrs={"placeholder": "nombre@correo.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cargo"].queryset = Cargo.objects.filter(activo=True).order_by("nombre")
        self.fields["cargo"].empty_label = "Seleccione un cargo"
        self.fields["telefono"].required = True  # el contacto principal es obligatorio
        if not self.is_bound:
            self.initial.setdefault("fecha_ingreso", date.today())
        for nombre, campo in self.fields.items():
            clase = "form-select" if isinstance(campo.widget, forms.Select) else "form-control"
            campo.widget.attrs["class"] = clase
            if campo.required:
                campo.widget.attrs["required"] = True

        # Verificación en vivo (HTMX): avisa si la identificación es inválida o ya está registrada.
        verificacion = {
            "hx-get": reverse("personal:verificar_identificacion"),
            "hx-include": "[name='tipo_identificacion'], [name='identificacion']",
            "hx-target": "#estado-identificacion",
            "hx-sync": "closest form:abort",
        }
        self.fields["identificacion"].widget.attrs.update(
            verificacion | {"hx-trigger": "input changed delay:400ms, blur", "aria-describedby": "estado-identificacion"}
        )
        self.fields["tipo_identificacion"].widget.attrs.update(verificacion | {"hx-trigger": "change"})

    def is_valid(self):
        valido = super().is_valid()
        for nombre in self.errors:
            if nombre in self.fields:
                widget = self.fields[nombre].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()
                widget.attrs["aria-invalid"] = "true"
        return valido

    # ------------------------------------------------------------- campos individuales
    def clean_identificacion(self):
        return validadores.normalizar_identificacion(self.cleaned_data["identificacion"])

    def clean_nombres(self):
        valor = validadores.normalizar_nombre(self.cleaned_data["nombres"])
        validadores.validar_nombre(valor)
        return valor

    def clean_apellidos(self):
        valor = validadores.normalizar_nombre(self.cleaned_data["apellidos"])
        validadores.validar_nombre(valor)
        return valor

    def clean_telefono(self):
        valor = validadores.normalizar_telefono(self.cleaned_data["telefono"])
        validadores.validar_telefono(valor)
        return valor

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_direccion(self):
        return " ".join((self.cleaned_data.get("direccion") or "").split())

    def clean_fecha_nacimiento(self):
        valor = self.cleaned_data.get("fecha_nacimiento")
        if valor and valor >= date.today():
            raise ValidationError("La fecha de nacimiento debe ser anterior a hoy.")
        return valor

    def clean_fecha_ingreso(self):
        valor = self.cleaned_data["fecha_ingreso"]
        if valor > date.today() + timedelta(days=365):
            raise ValidationError("La fecha de ingreso no puede ser más de un año en el futuro.")
        return valor

    # ------------------------------------------------------------- reglas entre campos
    def clean(self):
        datos = super().clean()
        tipo, identificacion = datos.get("tipo_identificacion"), datos.get("identificacion")

        if tipo and identificacion:
            try:
                validadores.validar_identificacion(tipo, identificacion)
            except ValidationError as error:
                self.add_error("identificacion", error)
            else:
                existente = Empleado.objects.filter(tipo_identificacion=tipo, identificacion=identificacion)
                if self.instance.pk:
                    existente = existente.exclude(pk=self.instance.pk)
                existente = existente.first()
                if existente:
                    self.add_error("identificacion", ValidationError(
                        "Ya existe un empleado registrado con esta identificación: %(nombre)s.",
                        code="duplicado", params={"nombre": existente},
                    ))

        nacimiento, ingreso = datos.get("fecha_nacimiento"), datos.get("fecha_ingreso")
        if nacimiento and ingreso and ingreso <= nacimiento:
            self.add_error("fecha_ingreso", "La fecha de ingreso debe ser posterior a la fecha de nacimiento.")
        return datos
