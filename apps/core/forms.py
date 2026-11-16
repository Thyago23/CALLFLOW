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

    def clean(self):
        # Tras varios intentos fallidos la cuenta queda bloqueada unos minutos (ver apps.core.limite_peticiones).
        from . import limite_peticiones

        usuario = self.cleaned_data.get("username") or ""
        segundos = limite_peticiones.ingreso_bloqueado(usuario)
        if segundos:
            minutos = max(1, round(segundos / 60))
            raise ValidationError(f"Demasiados intentos fallidos. Espere {minutos} minuto{'s' if minutos != 1 else ''} "
                                  "y vuelva a intentarlo.", code="bloqueado")
        # Los fallos se cuentan con la señal user_login_failed y el bloqueo lo aplica el backend.
        return super().clean()

    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise ValidationError(self.error_messages["inactive"], code="inactive")


class UsuarioCrearForm(FormularioEstilizado, forms.ModelForm):
    """El administrador crea la cuenta; el sistema genera el código y la contraseña temporal."""

    empleado = forms.ModelChoiceField(
        label="Empleado vinculado", required=False, queryset=Empleado.objects.none(),
        empty_label="Sin vincular",
        help_text="Une la cuenta con su ficha en Personal. Obligatorio para asesores de ventas y supervisores.",
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
        # Las cuentas de TI son fijas del sistema (CUENTAS_TI): no se crean desde aquí.
        self.fields["rol"].choices = [(v, e) for v, e in self.fields["rol"].choices if v != Rol.TI]
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


# --------------------------------------------------------------------------- mi perfil
FOTO_MAXIMO_BYTES = 5 * 1024 * 1024
FOTO_LADO = 320  # se guarda cuadrada, 320×320 (alcanza para la barra superior y el tablero de la TV)


def procesar_foto(archivo):
    """
    Abre la imagen con Pillow, la recorta al centro en un cuadrado y la vuelve a guardar como JPEG.
    Así se descarta lo que no sea imagen, se quitan los metadatos (ubicación GPS, cámara) y pesa poco.
    """
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps, UnidentifiedImageError

    if archivo.size > FOTO_MAXIMO_BYTES:
        raise ValidationError("La foto pesa más de 5 MB. Elija una más liviana.")
    try:
        with Image.open(archivo) as imagen:
            if imagen.width * imagen.height > 40_000_000:
                raise ValidationError("La foto es demasiado grande (más de 40 megapíxeles).")
            imagen = ImageOps.exif_transpose(imagen)  # respeta la orientación con que se tomó en el celular
            imagen = ImageOps.fit(imagen.convert("RGB"), (FOTO_LADO, FOTO_LADO), Image.Resampling.LANCZOS)
            salida = BytesIO()
            imagen.save(salida, "JPEG", quality=86, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValidationError("El archivo no es una imagen válida. Use una foto JPG, PNG o WEBP.") from error
    return ContentFile(salida.getvalue(), name="perfil.jpg")


class PerfilForm(FormularioEstilizado, forms.ModelForm):
    foto_nueva = forms.FileField(
        label="Foto de perfil", required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="JPG, PNG o WEBP de hasta 5 MB. Se recorta en un cuadrado al centro.",
    )
    quitar_foto = forms.BooleanField(label="Quitar mi foto", required=False)

    class Meta:
        model = Usuario
        fields = ["apodo", "tema"]
        widgets = {"tema": forms.RadioSelect}
        help_texts = {"apodo": "Cómo lo verán sus compañeros dentro de CALLFLOW (barra superior y tablero de ventas). "
                               "Déjelo vacío para usar su nombre."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["apodo"].widget.attrs.update({"class": "form-control", "maxlength": 30, "placeholder": "Ej.: Lu, El Capi"})
        self.fields["foto_nueva"].widget.attrs["class"] = "form-control"
        self.fields["quitar_foto"].widget.attrs["class"] = "form-check-input"

    def clean_apodo(self):
        valor = " ".join((self.cleaned_data.get("apodo") or "").split())
        if valor and not any(c.isalnum() for c in valor):
            raise ValidationError("El apodo debe tener al menos una letra o número.")
        return valor

    def clean_foto_nueva(self):
        archivo = self.cleaned_data.get("foto_nueva")
        return procesar_foto(archivo) if archivo else None

    def save(self, commit=True):
        usuario = super().save(commit=False)
        anterior = usuario.foto.name if usuario.foto else ""
        nueva = self.cleaned_data.get("foto_nueva")
        if nueva:
            usuario.foto.save(nueva.name, nueva, save=False)
        elif self.cleaned_data.get("quitar_foto"):
            usuario.foto = ""
        if commit:
            usuario.save()
            if anterior and anterior != (usuario.foto.name if usuario.foto else ""):
                usuario.foto.storage.delete(anterior)  # no se acumulan fotos viejas
        return usuario
