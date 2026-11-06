import re

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core import validadores
from apps.core.forms import FormularioEstilizado, _estilizar
from apps.personal.models import Empleado

from . import tarjetas
from .models import BaseComercial, EntidadEmisora, RangoBIN, TarjetaDAMI

# Mismo orden que el DAMI en papel: Diners | Mastercard / Visa | American Express
_MARCAS_COMO_EN_PAPEL = [(m, TarjetaDAMI.Marca(m).label) for m in ("DINERS", "MASTERCARD", "VISA", "AMEX")]

_POSIBLE_TARJETA = re.compile(r"(?:\d[ -]?){13,19}")


def sin_numero_de_tarjeta(texto: str) -> str:
    """Por seguridad, el número de tarjeta solo se acepta en su campo (que se cifra), nunca en textos libres."""
    for coincidencia in _POSIBLE_TARJETA.finditer(texto or ""):
        digitos = re.sub(r"\D", "", coincidencia.group())
        if 13 <= len(digitos) <= 19 and tarjetas.luhn_valido(digitos):
            raise ValidationError("No escriba números de tarjeta aquí: va únicamente en el campo «Tarjeta No».")
    return texto


class FormularioDAMIForm(FormularioEstilizado, forms.Form):
    """
    Formulario DAMI con los mismos campos del formato en papel.
    Fecha, código único y asesor los pone el sistema.
    """

    cedula = forms.CharField(label="Cédula", max_length=13, required=False)
    ruc = forms.CharField(label="RUC", max_length=13, required=False)
    # La hoja de producción lleva el nombre del cliente, así que se pide aquí.
    nombres = forms.CharField(label="Nombres del cliente", max_length=100,
                              help_text="Si solo tiene RUC, la razón social.")
    apellidos = forms.CharField(label="Apellidos", max_length=100, required=False)
    telefono = forms.CharField(label="Telf", max_length=20)
    telefono_alterno = forms.CharField(label="Telf2", max_length=20, required=False)
    telefono3 = forms.CharField(label="Telf3", max_length=20, required=False)
    direccion = forms.CharField(label="Dirección", max_length=255, required=False)
    sector = forms.CharField(label="Sector", max_length=120, required=False)
    ciudad = forms.CharField(label="Ciudad", max_length=80, required=False)
    referencia = forms.CharField(label="Referencia", max_length=255, required=False)
    email = forms.EmailField(label="Email", required=False)

    # Tarjetas para realizar el pago
    marca = forms.ChoiceField(label="Tarjeta", required=False, choices=_MARCAS_COMO_EN_PAPEL, widget=forms.RadioSelect)
    banco = forms.CharField(label="Banco", max_length=80, required=False)
    caducidad = forms.CharField(label="Caducidad", max_length=7, required=False,
                                widget=forms.TextInput(attrs={"placeholder": "MM/AA", "inputmode": "numeric",
                                                             "maxlength": "5", "data-caducidad": ""}))
    numero_tarjeta = forms.CharField(
        label="Tarjeta No", max_length=23, required=False,
        widget=forms.PasswordInput(render_value=False, attrs={
            "inputmode": "numeric", "autocomplete": "off", "placeholder": "Número completo de la tarjeta"}),
    )
    observaciones = forms.CharField(label="Observación", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, tarjeta_guardada=None, **kwargs):
        """`tarjeta_guardada`: la tarjeta que ya tiene el borrador (de ella solo se muestran los últimos 4 dígitos)."""
        super().__init__(*args, **kwargs)
        self.tarjeta_guardada = tarjeta_guardada
        _estilizar(self)
        self.fields["marca"].widget.attrs["class"] = "btn-check"
        if tarjeta_guardada:
            self.fields["numero_tarjeta"].widget.attrs["placeholder"] = (
                f"**** {tarjeta_guardada.ultimos4} · escriba el número solo si cambia la tarjeta")
        buscar = {
            "hx-get": reverse("ventas:buscar_cliente"),
            "hx-include": "[name='cedula'], [name='ruc']",
            "hx-target": "#estado-cliente",
            "hx-trigger": "input changed delay:400ms, blur",
        }
        for campo in ("cedula", "ruc"):
            self.fields[campo].widget.attrs.update(buscar | {"autocomplete": "off", "inputmode": "numeric"})
        for campo in ("telefono", "telefono_alterno", "telefono3"):
            self.fields[campo].widget.attrs["inputmode"] = "tel"
        self.fields["telefono"].widget.attrs["placeholder"] = "0991234567"
        self.fields["banco"].widget.attrs["list"] = "entidades-emisoras"
        self.entidades = EntidadEmisora.objects.filter(activa=True).values_list("nombre", flat=True).order_by("nombre")

    @classmethod
    def desde_formulario(cls, f, **kwargs):
        c = f.cliente
        tarjeta = getattr(f, "tarjeta", None)
        inicial = {
            "cedula": c.cedula, "ruc": c.ruc_mostrar, "nombres": c.nombres, "apellidos": c.apellidos, "telefono": c.telefono, "telefono_alterno": c.telefono_alterno,
            "telefono3": c.telefono3, "direccion": c.direccion, "sector": c.sector, "ciudad": c.ciudad,
            "referencia": c.referencia, "email": c.email, "observaciones": f.observaciones,
        }
        if tarjeta:
            inicial |= {"marca": tarjeta.marca, "banco": tarjeta.banco, "caducidad": tarjeta.caducidad}
        return cls(initial=inicial, tarjeta_guardada=tarjeta, **kwargs)

    # ------------------------------------------------------------- identificación
    def clean_cedula(self):
        valor = validadores.normalizar_identificacion(self.cleaned_data.get("cedula") or "")
        if valor:
            validadores.validar_identificacion("CEDULA", valor)
        return valor

    def clean_ruc(self):
        valor = validadores.normalizar_identificacion(self.cleaned_data.get("ruc") or "")
        if not valor:
            return valor
        if not re.fullmatch(r"\d{13}", valor):
            raise ValidationError("El RUC debe tener 13 dígitos.")
        provincia_valida = 1 <= int(valor[:2]) <= 24 or valor[:2] == "30"
        if not provincia_valida or valor[10:] == "000":
            raise ValidationError("El RUC no es válido. Revise que esté bien escrito.")
        return valor

    # ------------------------------------------------------------- contacto
    def _telefono(self, campo, obligatorio):
        valor = validadores.normalizar_telefono(self.cleaned_data.get(campo) or "")
        if valor or obligatorio:
            validadores.validar_telefono(valor)
        return valor

    def clean_telefono(self):
        return self._telefono("telefono", True)

    def clean_telefono_alterno(self):
        return self._telefono("telefono_alterno", False)

    def clean_telefono3(self):
        return self._telefono("telefono3", False)

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_nombres(self):
        return sin_numero_de_tarjeta(validadores.normalizar_nombre(self.cleaned_data.get("nombres") or ""))

    def clean_apellidos(self):
        return sin_numero_de_tarjeta(validadores.normalizar_nombre(self.cleaned_data.get("apellidos") or ""))

    def clean_ciudad(self):
        return validadores.normalizar_nombre(self.cleaned_data.get("ciudad") or "")

    def _texto(self, campo):
        return sin_numero_de_tarjeta(" ".join((self.cleaned_data.get(campo) or "").split()))

    def clean_direccion(self):
        return self._texto("direccion")

    def clean_sector(self):
        return self._texto("sector")

    def clean_referencia(self):
        return self._texto("referencia")

    def clean_banco(self):
        return self._texto("banco")

    def clean_observaciones(self):
        return sin_numero_de_tarjeta((self.cleaned_data.get("observaciones") or "").strip())

    # ------------------------------------------------------------- reglas entre campos
    def clean(self):
        datos = super().clean()
        if not datos.get("cedula") and not datos.get("ruc") and "cedula" not in self.errors and "ruc" not in self.errors:
            self.add_error("cedula", "Ingrese la cédula o el RUC del cliente.")

        marca, numero = datos.get("marca"), datos.get("numero_tarjeta") or ""
        hay_tarjeta = bool(marca or datos.get("banco") or datos.get("caducidad") or numero or self.tarjeta_guardada)
        datos["tarjeta"] = None
        if not hay_tarjeta:
            return datos

        if not marca:
            self.add_error("marca", "Marque la tarjeta con la que se realizará el pago.")
        if not datos.get("banco"):
            self.add_error("banco", "Indique el banco.")
        mes = anio = None
        try:
            mes, anio = tarjetas.validar_caducidad(datos.get("caducidad") or "")
        except ValidationError as error:
            self.add_error("caducidad", error)
        numero_valido = None
        if numero:
            if marca:
                try:
                    numero_valido = tarjetas.validar_numero(marca, numero)
                except ValidationError as error:
                    self.add_error("numero_tarjeta", error)
        elif not self.tarjeta_guardada:
            self.add_error("numero_tarjeta", "Ingrese el número de la tarjeta.")
        elif marca and marca != self.tarjeta_guardada.marca:
            self.add_error("numero_tarjeta", "Si cambia la marca, vuelva a escribir el número de la tarjeta.")
        datos["tarjeta"] = {"marca": marca, "banco": datos.get("banco"), "mes": mes, "anio": anio, "numero": numero_valido}
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


# --------------------------------------------------------------------------- validación de tarjetas
_MARCAS_TODAS = [(c, m.nombre) for c, m in tarjetas.MARCAS.items()]


class RangoBINForm(FormularioEstilizado, forms.ModelForm):
    marca = forms.ChoiceField(label="Marca", choices=[("", "Seleccione…"), *_MARCAS_TODAS])

    class Meta:
        model = RangoBIN
        fields = ["bin", "entidad", "marca", "tipo_tarjeta"]
        labels = {"bin": "BIN (6 a 8 dígitos)", "entidad": "Banco o cooperativa", "tipo_tarjeta": "Tipo"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["entidad"].queryset = EntidadEmisora.objects.filter(activa=True)
        self.fields["entidad"].empty_label = "Seleccione…"
        self.fields["bin"].widget.attrs.update({"inputmode": "numeric", "maxlength": "8", "autocomplete": "off"})
        _estilizar(self)

    def clean_bin(self):
        valor = tarjetas.solo_digitos(self.cleaned_data.get("bin") or "")
        if not re.fullmatch(r"\d{6,8}", valor):
            raise ValidationError("El BIN son los primeros 6 a 8 dígitos de la tarjeta.")
        return valor

    def clean(self):
        datos = super().clean()
        if datos.get("bin") and datos.get("marca"):
            detectada = tarjetas.detectar_marca(datos["bin"])
            if detectada != datos["marca"]:
                self.add_error("marca", f"Ese BIN es de {tarjetas.MARCAS[detectada].nombre}." if detectada
                               else "Ese BIN no corresponde a ninguna marca conocida.")
        return datos


class EntidadEmisoraForm(FormularioEstilizado, forms.ModelForm):
    class Meta:
        model = EntidadEmisora
        fields = ["nombre", "tipo"]
        labels = {"nombre": "Nombre", "tipo": "Tipo"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)

    def clean_nombre(self):
        nombre = " ".join((self.cleaned_data.get("nombre") or "").split())
        if EntidadEmisora.objects.filter(nombre__iexact=nombre).exists():
            raise ValidationError("Esa entidad ya está en el catálogo.")
        return nombre


class ImportarBINForm(FormularioEstilizado, forms.Form):
    archivo = forms.FileField(label="Archivo CSV", help_text="Columnas: bin, entidad, marca, tipo. Separadas por coma o punto y coma.",
                              widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if not archivo.name.lower().endswith(".csv"):
            raise ValidationError("Suba un archivo .csv.")
        if archivo.size > 2 * 1024 * 1024:
            raise ValidationError("El archivo supera 2 MB.")
        return archivo
