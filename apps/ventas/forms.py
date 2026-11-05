import re

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core import validadores
from apps.core.forms import FormularioEstilizado, _estilizar
from apps.personal.models import Empleado

from .models import BaseComercial, Cliente

_POSIBLE_TARJETA = re.compile(r"(?:\d[ -]?){13,19}")


def _luhn(numero: str) -> bool:
    suma, alternar = 0, False
    for d in reversed(numero):
        n = int(d)
        if alternar:
            n = n * 2 - 9 if n > 4 else n * 2
        suma += n
        alternar = not alternar
    return suma % 10 == 0


def sin_numero_de_tarjeta(texto: str) -> str:
    """Por seguridad (SCRUM-40) no se guardan números de tarjeta en ningún campo de texto."""
    for coincidencia in _POSIBLE_TARJETA.finditer(texto or ""):
        digitos = re.sub(r"\D", "", coincidencia.group())
        if 13 <= len(digitos) <= 19 and _luhn(digitos):
            raise ValidationError("No escriba números de tarjeta: solo marque si el cliente tiene una tarjeta activa.")
    return texto


class FormularioDAMIForm(FormularioEstilizado, forms.Form):
    """SCRUM-38 · Formulario DAMI digital (campos base; los definitivos dependen de SCRUM-37)."""

    tipo_identificacion = forms.ChoiceField(label="Tipo de identificación", choices=Cliente.TipoIdentificacion.choices)
    identificacion = forms.CharField(label="Identificación", max_length=20)
    nombres = forms.CharField(label="Nombres", max_length=100)
    apellidos = forms.CharField(label="Apellidos", max_length=100)
    telefono = forms.CharField(label="Teléfono", max_length=20)
    telefono_alterno = forms.CharField(label="Teléfono alterno", max_length=20, required=False)
    email = forms.EmailField(label="Correo electrónico", required=False)
    ciudad = forms.CharField(label="Ciudad", max_length=80, required=False)
    direccion = forms.CharField(label="Dirección", max_length=255, required=False)
    producto = forms.CharField(label="Producto o servicio", max_length=120, required=False,
                               help_text="Obligatorio para enviar a cierre.")
    tiene_tarjeta_activa = forms.BooleanField(
        label="El cliente tiene una tarjeta activa", required=False,
        help_text="Se atenderá con prioridad. No escriba el número de la tarjeta en ningún campo.",
    )
    base_comercial = forms.ModelChoiceField(label="Base comercial", queryset=BaseComercial.objects.none(), required=False,
                                            empty_label="Sin base / referido")
    observaciones = forms.CharField(label="Observaciones", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, bases=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["base_comercial"].queryset = bases if bases is not None else BaseComercial.objects.none()
        _estilizar(self)
        self.fields["tiene_tarjeta_activa"].widget.attrs["class"] = "form-check-input"
        verificar = {
            "hx-get": reverse("ventas:buscar_cliente"),
            "hx-include": "[name='tipo_identificacion'], [name='identificacion']",
            "hx-target": "#estado-cliente",
        }
        self.fields["identificacion"].widget.attrs.update(verificar | {"hx-trigger": "input changed delay:400ms, blur",
                                                                       "autocomplete": "off", "inputmode": "numeric"})
        self.fields["tipo_identificacion"].widget.attrs.update(verificar | {"hx-trigger": "change"})
        self.fields["telefono"].widget.attrs.update({"inputmode": "tel", "placeholder": "0991234567"})

    @classmethod
    def desde_formulario(cls, f, **kwargs):
        c = f.cliente
        inicial = {
            "tipo_identificacion": c.tipo_identificacion, "identificacion": c.identificacion, "nombres": c.nombres,
            "apellidos": c.apellidos, "telefono": c.telefono, "telefono_alterno": c.telefono_alterno, "email": c.email,
            "ciudad": c.ciudad, "direccion": c.direccion, "producto": f.producto,
            "tiene_tarjeta_activa": f.tiene_tarjeta_activa, "base_comercial": f.base_comercial_id,
            "observaciones": f.observaciones,
        }
        return cls(initial=inicial, **kwargs)

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

    def _telefono(self, campo, obligatorio):
        valor = validadores.normalizar_telefono(self.cleaned_data.get(campo) or "")
        if valor or obligatorio:
            validadores.validar_telefono(valor)
        return valor

    def clean_telefono(self):
        return self._telefono("telefono", True)

    def clean_telefono_alterno(self):
        return self._telefono("telefono_alterno", False)

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_ciudad(self):
        return validadores.normalizar_nombre(self.cleaned_data.get("ciudad") or "")

    def clean_direccion(self):
        return sin_numero_de_tarjeta(" ".join((self.cleaned_data.get("direccion") or "").split()))

    def clean_producto(self):
        return sin_numero_de_tarjeta(" ".join((self.cleaned_data.get("producto") or "").split()))

    def clean_observaciones(self):
        return sin_numero_de_tarjeta((self.cleaned_data.get("observaciones") or "").strip())

    def clean(self):
        datos = super().clean()
        tipo, identificacion = datos.get("tipo_identificacion"), datos.get("identificacion")
        if tipo and identificacion:
            try:
                validadores.validar_identificacion(tipo, identificacion)
            except ValidationError as error:
                self.add_error("identificacion", error)
        return datos


class SolicitudBaseForm(FormularioEstilizado, forms.Form):
    mensaje = forms.CharField(label="Mensaje (opcional)", required=False, max_length=500,
                              widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Ej.: terminé la base de Quito norte"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)


class BaseComercialForm(FormularioEstilizado, forms.ModelForm):
    class Meta:
        model = BaseComercial
        fields = ["nombre", "descripcion", "total_registros", "activa"]
        labels = {"nombre": "Nombre", "descripcion": "Descripción", "total_registros": "Número de registros", "activa": "Activa"}
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)
        self.fields["activa"].widget.attrs["class"] = "form-check-input"


class AsignarBaseForm(FormularioEstilizado, forms.Form):
    base = forms.ModelChoiceField(label="Base", queryset=BaseComercial.objects.filter(activa=True).order_by("nombre"),
                                  empty_label="Seleccione…")
    vendedores = forms.ModelMultipleChoiceField(label="Vendedores", queryset=Empleado.objects.none(),
                                                widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendedores"].queryset = Empleado.objects.filter(
            usuario__rol="VENDEDOR", estado=Empleado.Estado.ACTIVO).order_by("apellidos", "nombres")
        _estilizar(self)
        self.fields["vendedores"].widget.attrs["class"] = "form-check-input"
