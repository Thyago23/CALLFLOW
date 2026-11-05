from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError

from . import validadores
from .models import Rol, Usuario


def _estilizar(form):
    for campo in form.fields.values():
        clase = "form-select" if isinstance(campo.widget, forms.Select) else "form-control"
        campo.widget.attrs["class"] = clase


def _marcar_invalidos(form):
    for nombre in form.errors:
        if nombre in form.fields:
            widget = form.fields[nombre].widget
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()
            widget.attrs["aria-invalid"] = "true"


class IngresoForm(AuthenticationForm):
    """Login que distingue 'contraseña incorrecta' de 'cuenta pendiente de aprobación'."""

    error_messages = {
        "invalid_login": "Usuario o contraseña incorrectos.",
        "inactive": "Su cuenta está desactivada. Comuníquese con el administrador.",
        "pendiente": "Su cuenta todavía está pendiente de aprobación por un administrador.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuario"
        self.fields["password"].label = "Contraseña"
        _estilizar(self)

    def clean(self):
        username, password = self.cleaned_data.get("username"), self.cleaned_data.get("password")
        if username and password and authenticate(self.request, username=username, password=password) is None:
            usuario = Usuario.objects.filter(username__iexact=username).first()
            if usuario is not None and not usuario.is_active and usuario.check_password(password):
                codigo = "pendiente" if usuario.pendiente_aprobacion else "inactive"
                raise ValidationError(self.error_messages[codigo], code=codigo)
        return super().clean()


class RegistroForm(UserCreationForm):
    """Solicitud de cuenta. El rol de Administrador no se puede elegir: lo asigna otro administrador."""

    ROLES_SOLICITABLES = [(Rol.VENDEDOR, Rol.VENDEDOR.label), (Rol.ENCARGADO_CIERRE, Rol.ENCARGADO_CIERRE.label)]

    first_name = forms.CharField(label="Nombres", max_length=100)
    last_name = forms.CharField(label="Apellidos", max_length=100)
    email = forms.EmailField(label="Correo electrónico")
    rol = forms.ChoiceField(label="Cargo que desempeña", choices=ROLES_SOLICITABLES)

    class Meta(UserCreationForm.Meta):
        model = Usuario
        fields = ("first_name", "last_name", "username", "email", "rol")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuario"
        self.fields["username"].help_text = "Con este nombre ingresará al sistema. Letras, números y @ . + - _"
        self.fields["password1"].label = "Contraseña"
        self.fields["password1"].help_text = "Mínimo 10 caracteres; no use solo números ni contraseñas comunes."
        self.fields["password2"].label = "Repita la contraseña"
        self.fields["password2"].help_text = ""
        autocompletar = {"first_name": "given-name", "last_name": "family-name", "username": "username",
                         "email": "email", "password1": "new-password", "password2": "new-password"}
        for nombre, valor in autocompletar.items():
            self.fields[nombre].widget.attrs["autocomplete"] = valor
        _estilizar(self)

    def is_valid(self):
        valido = super().is_valid()
        _marcar_invalidos(self)
        return valido

    def clean_first_name(self):
        valor = validadores.normalizar_nombre(self.cleaned_data["first_name"])
        validadores.validar_nombre(valor)
        return valor

    def clean_last_name(self):
        valor = validadores.normalizar_nombre(self.cleaned_data["last_name"])
        validadores.validar_nombre(valor)
        return valor

    def clean_username(self):
        valor = self.cleaned_data["username"].strip()
        if Usuario.objects.filter(username__iexact=valor).exists():
            raise ValidationError("Ese nombre de usuario ya está en uso.")
        return valor

    def clean_email(self):
        valor = self.cleaned_data["email"].strip().lower()
        if Usuario.objects.filter(email__iexact=valor).exists():
            raise ValidationError("Ya existe una cuenta con ese correo electrónico.")
        return valor


class AprobacionForm(forms.Form):
    rol = forms.ChoiceField(label="Rol", choices=Rol.choices)
