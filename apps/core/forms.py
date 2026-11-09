from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.core.exceptions import ValidationError

from apps.personal.models import Empleado

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


class FormularioEstilizado:
    def is_valid(self):
        valido = super().is_valid()
        _marcar_invalidos(self)
        return valido


class IngresoForm(AuthenticationForm):
    """Ingreso con nombre.apellido + código de acceso (ver apps.core.backends)."""

    error_messages = {
        "invalid_login": "Usuario o código de acceso incorrectos.",
        "inactive": "Su cuenta está desactivada. Comuníquese con el administrador.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuario"
        self.fields["username"].widget.attrs.update({"placeholder": "nombre.apellido", "autocapitalize": "none", "spellcheck": "false"})
        self.fields["password"].label = "Código de acceso"
        self.fields["password"].widget.attrs.update({"placeholder": "Ej.: LM48207153", "autocapitalize": "characters"})
        _estilizar(self)

    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise ValidationError(self.error_messages["inactive"], code="inactive")


class UsuarioCrearForm(FormularioEstilizado, forms.ModelForm):
    """El administrador crea la cuenta; el sistema genera el código y la contraseña temporal."""

    empleado = forms.ModelChoiceField(
        label="Empleado vinculado", required=False, queryset=Empleado.objects.none(),
        empty_label="Sin vincular",
        help_text="Une la cuenta con su ficha en Personal. Obligatorio para vendedores y encargados de cierre.",
    )
    # Ventas y cierre registran el trabajo a nombre del empleado: sin ficha no pueden gestionar.
    ROLES_CON_FICHA = (Rol.VENDEDOR, Rol.ENCARGADO_CIERRE)

    class Meta:
        model = Usuario
        fields = ("first_name", "last_name", "email", "rol")
        labels = {"first_name": "Nombres", "last_name": "Apellidos", "email": "Correo electrónico", "rol": "Rol"}
        help_texts = {"email": "Opcional."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.fields["empleado"].queryset = (
            Empleado.objects.filter(usuario__isnull=True).exclude(estado=Empleado.Estado.DESVINCULADO).order_by("apellidos", "nombres")
        )
        _estilizar(self)

    def clean_first_name(self):
        valor = validadores.normalizar_nombre(self.cleaned_data["first_name"])
        validadores.validar_nombre(valor)
        return valor

    def clean_last_name(self):
        valor = validadores.normalizar_nombre(self.cleaned_data["last_name"])
        validadores.validar_nombre(valor)
        return valor

    def clean_email(self):
        valor = (self.cleaned_data.get("email") or "").strip().lower() or None
        if valor and Usuario.objects.filter(email__iexact=valor).exists():
            raise ValidationError("Ya existe un usuario con ese correo electrónico.")
        return valor

    def clean(self):
        datos = super().clean()
        if datos.get("rol") in self.ROLES_CON_FICHA and not datos.get("empleado"):
            self.add_error("empleado", "Vincule la cuenta a su ficha de empleado: sin ella no podrá gestionar ventas ni "
                                       "cierres. Si aún no está registrado, regístrelo en Personal → Nuevo empleado "
                                       "(se le crea la cuenta automáticamente).")
        return datos


class CambioClaveForm(FormularioEstilizado, SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "Nueva contraseña"
        self.fields["new_password1"].help_text = "Mínimo 10 caracteres; no use solo números, su código ni contraseñas comunes."
        self.fields["new_password2"].label = "Repita la nueva contraseña"
        _estilizar(self)
