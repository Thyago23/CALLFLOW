import json
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse

from apps.core import validadores
from apps.core.cuentas import rol_para_cargo
from apps.core.models import Rol
from apps.documentos import archivos

from .models import Cargo, Empleado, EsquemaComision, ObservacionEmpleado


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

    salario_base = forms.DecimalField(
        label="Sueldo base (USD)", required=False, max_digits=12, decimal_places=2, min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
        help_text="Se puede cambiar después en la ficha; el cambio queda en el historial.",
    )
    equipo = forms.ModelChoiceField(
        EsquemaComision.objects.none(), label="Equipo", required=False, empty_label="Administrativo",
        help_text="Vendedores: Visado o Paquetería (define sus comisiones). Administrativo: no lleva comisiones.",
    )
    cuenta_existente = forms.ModelChoiceField(
        queryset=None, label="Cuenta de acceso", required=False, empty_label="Crear una cuenta nueva",
        help_text="Si la persona ya tiene usuario en CALLFLOW (creado desde Usuarios), elíjalo para vincularlo "
                  "en lugar de crear otra cuenta.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipo"].queryset = _esquemas_activos()
        from apps.core.models import Usuario

        self.fields["cuenta_existente"].queryset = (
            Usuario.objects.filter(is_active=True, empleado__isnull=True).order_by("first_name", "last_name"))
        self.fields["cuenta_existente"].label_from_instance = (
            lambda u: f"{u.get_full_name() or u.username} ({u.username} · {u.get_rol_display()})")
        if not self.is_bound and not self.instance.pk:
            from apps.nomina.calculo import sueldo_base_por_defecto
            self.initial.setdefault("salario_base", sueldo_base_por_defecto())
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

        # Las cuentas fijas de TI no pueden cambiar de rol (la base lo impide): el cargo debe ser de TI.
        cuenta = datos.get("cuenta_existente") or (self.instance.usuario if self.instance.pk else None)
        cargo = datos.get("cargo")
        if cuenta is not None and cuenta.protegida and cargo and rol_para_cargo(cargo.nombre) != Rol.TI:
            self.add_error("cargo", f"La cuenta {cuenta.username} es una cuenta fija del departamento de TI: "
                                    "el empleado debe tener un cargo de TI, como «Tecnología (TI)».")
        return datos


class DocumentosIngresoForm(forms.Form):
    """Expediente de ingreso: CV, copia de cédula y título de bachiller (PDF, JPG o PNG, máx. 10 MB)."""

    # Campo del formulario -> código del tipo de documento
    TIPOS = {"hoja_vida": "HOJA_VIDA", "cedula": "CEDULA", "titulo_bachiller": "TITULO_BACHILLER"}

    hoja_vida = forms.FileField(
        label="Hoja de vida (CV)",
        help_text="En PDF, el sistema lee el CV y llena los datos que encuentre; revíselos antes de guardar.",
    )
    cedula = forms.FileField(label="Copia de cédula")
    titulo_bachiller = forms.FileField(label="Título de bachiller")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.widget.attrs.update({"class": "form-control", "accept": ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"})
            campo.widget.attrs["required"] = True
        self.fields["hoja_vida"].widget.attrs.update({
            "hx-post": reverse("personal:leer_cv"),
            "hx-trigger": "change",
            "hx-encoding": "multipart/form-data",
            "hx-params": "hoja_vida",
            "hx-target": "#estado-cv",
            "hx-indicator": "#estado-cv",
            "hx-sync": "this:replace",
            "aria-describedby": "estado-cv",
        })

    def is_valid(self):
        valido = super().is_valid()
        for nombre in self.errors:
            if nombre in self.fields:
                widget = self.fields[nombre].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()
        return valido

    def _validar(self, nombre):
        archivo = self.cleaned_data[nombre]
        archivos.validar_archivo(archivo)
        return archivo

    def clean_hoja_vida(self):
        return self._validar("hoja_vida")

    def clean_cedula(self):
        return self._validar("cedula")

    def clean_titulo_bachiller(self):
        return self._validar("titulo_bachiller")

    def clean(self):
        datos = super().clean()
        vistos = {}
        for nombre in self.TIPOS:
            archivo = datos.get(nombre)
            if not archivo:
                continue
            huella = archivos.huella(archivo)
            if huella in vistos:
                self.add_error(nombre, f"Es el mismo archivo que adjuntó en «{self.fields[vistos[huella]].label}».")
            else:
                vistos[huella] = nombre
        return datos


class ObservacionForm(forms.ModelForm):
    class Meta:
        model = ObservacionEmpleado
        fields = ["tipo", "texto"]
        labels = {"tipo": "Tipo", "texto": "Observación"}
        widgets = {"texto": forms.Textarea(attrs={"rows": 3, "maxlength": 2000, "placeholder": "Escriba la observación…"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tipo"].widget.attrs["class"] = "form-select"
        self.fields["texto"].widget.attrs["class"] = "form-control"

    def clean_texto(self):
        texto = (self.cleaned_data.get("texto") or "").strip()
        if not texto:
            raise ValidationError("Escriba la observación.")
        if len(texto) > 2000:
            raise ValidationError("La observación no puede pasar de 2000 caracteres.")
        return texto


class EmpleadoEditarForm(EmpleadoForm):
    """SCRUM-13 · Actualizar datos de empleados: las mismas validaciones del registro, más estado y salida."""

    class Meta(EmpleadoForm.Meta):
        fields = EmpleadoForm.Meta.fields + ["estado", "fecha_salida"]
        labels = EmpleadoForm.Meta.labels | {"estado": "Estado", "fecha_salida": "Fecha de salida"}
        widgets = EmpleadoForm.Meta.widgets | {
            "fecha_salida": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        help_texts = {"fecha_salida": "Obligatoria si el estado es «Desvinculado»."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sueldo y equipo se cambian desde la ficha (pestaña Sueldo y comisiones), con su historial.
        del self.fields["salario_base"], self.fields["equipo"], self.fields["cuenta_existente"]
        # El cargo actual se puede conservar aunque ya no esté activo para nuevos registros.
        cargo_actual = self.instance.cargo_id
        self.fields["cargo"].queryset = Cargo.objects.filter(Q(activo=True) | Q(pk=cargo_actual)).order_by("nombre")
        # La verificación en vivo no debe marcar como "duplicada" la identificación del propio empleado.
        hx_vals = json.dumps({"excluir": self.instance.pk})
        self.fields["identificacion"].widget.attrs["hx-vals"] = hx_vals
        self.fields["tipo_identificacion"].widget.attrs["hx-vals"] = hx_vals

    def clean(self):
        datos = super().clean()
        estado, salida, ingreso = datos.get("estado"), datos.get("fecha_salida"), datos.get("fecha_ingreso")
        if estado == Empleado.Estado.DESVINCULADO and not salida:
            self.add_error("fecha_salida", "Indique la fecha de salida del empleado desvinculado.")
        if salida and ingreso and salida < ingreso:
            self.add_error("fecha_salida", "La fecha de salida no puede ser anterior a la fecha de ingreso.")
        if salida and salida > date.today() + timedelta(days=365):
            self.add_error("fecha_salida", "La fecha de salida no puede ser más de un año en el futuro.")
        return datos


# --------------------------------------------------------------------------- SCRUM-14 · salarios y comisiones
def _esquemas_activos():
    return EsquemaComision.objects.filter(activo=True).order_by("nombre")


class SalarioForm(forms.Form):
    salario_base = forms.DecimalField(
        label="Nuevo sueldo base (USD)", max_digits=12, decimal_places=2, min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal", "class": "form-control"}),
    )
    vigente_desde = forms.DateField(label="Vigente desde", widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"))
    motivo = forms.CharField(label="Motivo", max_length=200, required=False,
                             widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej.: aumento por desempeño"}))

    def __init__(self, *args, empleado, **kwargs):
        self.empleado = empleado
        super().__init__(*args, **kwargs)
        self.initial.setdefault("vigente_desde", date.today())

    def clean_vigente_desde(self):
        desde = self.cleaned_data["vigente_desde"]
        if desde < self.empleado.fecha_ingreso:
            raise ValidationError("No puede ser anterior a la fecha de ingreso del empleado.")
        ultimo = self.empleado.salarios.order_by("-vigente_desde").first()
        if ultimo and desde <= ultimo.vigente_desde:
            raise ValidationError(f"Debe ser posterior al {ultimo.vigente_desde:%d/%m/%Y}, inicio del sueldo actual.")
        return desde


class EquipoForm(forms.Form):
    esquema = forms.ModelChoiceField(EsquemaComision.objects.none(), label="Equipo", empty_label=None,
                                     widget=forms.Select(attrs={"class": "form-select"}))
    vigente_desde = forms.DateField(label="Desde", widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"))

    def __init__(self, *args, empleado, **kwargs):
        self.empleado = empleado
        super().__init__(*args, **kwargs)
        self.fields["esquema"].queryset = _esquemas_activos()
        self.initial.setdefault("vigente_desde", date.today())

    def clean_vigente_desde(self):
        desde = self.cleaned_data["vigente_desde"]
        if desde < self.empleado.fecha_ingreso:
            raise ValidationError("No puede ser anterior a la fecha de ingreso del empleado.")
        ultima = self.empleado.comisiones.order_by("-vigente_desde").first()
        if ultima and desde <= ultima.vigente_desde:
            raise ValidationError(f"Debe ser posterior al {ultima.vigente_desde:%d/%m/%Y}, inicio del equipo actual.")
        return desde
