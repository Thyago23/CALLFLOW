import re
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core import validadores
from apps.core.concurrencia import ControlConcurrencia
from apps.core.forms import FormularioEstilizado, _estilizar
from apps.documentos.archivos import validar_archivo

from . import servicios
from .models import (ActivoFijo, Asiento, Cliente, CuentaContable, CuentaFinanciera, Lead, MovimientoFinanciero,
                     Proveedor, TipoIdentificacion)

_VALOR = {"min_value": Decimal("0.01"), "max_digits": 14, "decimal_places": 2}
_FECHA = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def ruc_valido(ruc: str) -> bool:
    """RUC de Ecuador (persona natural, sociedad o entidad pública): 13 dígitos, provincia válida y establecimiento."""
    if not re.fullmatch(r"\d{13}", ruc or "") or ruc[10:] == "000":
        return False
    if int(ruc[:2]) not in set(range(1, 25)) | {30}:
        return False
    tercero = int(ruc[2])
    return validadores.cedula_valida(ruc[:10]) if tercero < 6 else tercero in (6, 9)


def validar_identificacion(tipo: str, valor: str, obligatoria: bool = True) -> str:
    valor = validadores.normalizar_identificacion(valor)
    if not valor:
        if obligatoria:
            raise ValidationError("Ingrese la identificación.")
        return ""
    if tipo == TipoIdentificacion.CEDULA and not validadores.cedula_valida(valor):
        raise ValidationError("La cédula no es válida. Revise que tenga 10 dígitos y que estén bien escritos.")
    if tipo == TipoIdentificacion.RUC and not ruc_valido(valor):
        raise ValidationError("El RUC no es válido: 13 dígitos y termina en el número de establecimiento (001).")
    if tipo in (TipoIdentificacion.PASAPORTE, TipoIdentificacion.EXTERIOR) and not re.fullmatch(r"[A-Z0-9]{5,20}", valor):
        raise ValidationError("Use entre 5 y 20 letras o números.")
    return valor


def _telefono(valor: str) -> str:
    valor = validadores.normalizar_telefono(valor)
    if valor:
        validadores.validar_telefono(valor)
    return valor


class Base(FormularioEstilizado):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)
        for campo in self.fields.values():
            if isinstance(campo.widget, forms.CheckboxInput):
                campo.widget.attrs["class"] = "form-check-input"


# --------------------------------------------------------------------------- plan de cuentas
class CuentaContableForm(ControlConcurrencia, Base, forms.ModelForm):
    class Meta:
        model = CuentaContable
        fields = ["codigo", "nombre", "tipo", "naturaleza", "descripcion", "activa"]
        help_texts = {"codigo": "Con puntos, bajo una cuenta que ya exista. Ej.: 4.1.01.08",
                      "activa": "Una cuenta inactiva no se puede usar en asientos nuevos."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.lineas.exists():
            self.fields["codigo"].disabled = True
            self.fields["codigo"].help_text = "La cuenta ya tiene asientos: su código no cambia."

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip().strip(".")
        if not re.fullmatch(r"\d+(\.\d+)*", codigo):
            raise ValidationError("Use solo números separados por puntos. Ej.: 4.1.01.08")
        padre = codigo.rpartition(".")[0]
        if padre and not CuentaContable.objects.filter(codigo=padre).exists():
            raise ValidationError(f"No existe la cuenta de grupo {padre}: créela primero.")
        if CuentaContable.objects.filter(codigo=codigo).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Ya hay una cuenta con ese código.")
        return codigo


# --------------------------------------------------------------------------- caja y bancos
class CuentaFinancieraForm(ControlConcurrencia, Base, forms.ModelForm):
    class Meta:
        model = CuentaFinanciera
        fields = ["nombre", "tipo", "banco", "numero", "cuenta_contable", "saldo_inicial", "fecha_saldo_inicial", "activa"]
        widgets = {"fecha_saldo_inicial": _FECHA}
        help_texts = {"saldo_inicial": "Lo que había en la caja o en el banco a esa fecha (según el arqueo o el estado de cuenta).",
                      "cuenta_contable": "La cuenta del plan donde se refleja en el libro diario.",
                      "activa": "Una cuenta inactiva ya no admite movimientos."}

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields["cuenta_contable"].queryset = servicios.cuentas_de_movimiento(tipo=CuentaContable.Tipo.ACTIVO,
                                                                                    codigo__startswith="1.1.0")
        self.fields["cuenta_contable"].empty_label = "Seleccione…"
        if not self.instance.pk:
            self.fields["fecha_saldo_inicial"].initial = timezone.localdate()
        elif self.instance.movimientos.exists():
            for nombre in ("tipo", "saldo_inicial", "fecha_saldo_inicial"):
                self.fields[nombre].disabled = True
            self.fields["saldo_inicial"].help_text = "Ya tiene movimientos: el saldo inicial no cambia."

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        if CuentaFinanciera.objects.filter(empresa=self.empresa, nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise ValidationError(f"Ya hay una caja o banco llamado «{nombre}» en {self.empresa}.")
        return nombre

    def clean(self):
        datos = super().clean()
        if datos.get("tipo") and datos["tipo"] != CuentaFinanciera.Tipo.CAJA and not (datos.get("banco") or "").strip():
            self.add_error("banco", "Indique el banco.")
        if datos.get("tipo") == CuentaFinanciera.Tipo.CAJA and (datos.get("saldo_inicial") or 0) < 0:
            self.add_error("saldo_inicial", "La caja no puede empezar en negativo.")
        return datos


def _cuentas_de(empresa):
    return CuentaFinanciera.objects.filter(empresa=empresa, activa=True).order_by("tipo", "nombre")


class MovimientoForm(Base, forms.Form):
    """Ingreso, depósito, transferencia o egreso de una caja o banco."""

    cuenta = forms.ModelChoiceField(label="Caja o banco", queryset=CuentaFinanciera.objects.none(), empty_label="Seleccione…")
    tipo = forms.ChoiceField(label="Tipo de movimiento",
                             choices=[(v, e) for v, e in MovimientoFinanciero.Tipo.choices
                                      if v not in MovimientoFinanciero.TRASPASOS])
    fecha = forms.DateField(widget=_FECHA)
    valor = forms.DecimalField(label="Valor ($)", **_VALOR)
    concepto = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"placeholder": "Ej.: abono trámite visa EE. UU."}))
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), required=False, empty_label="—")
    proveedor = forms.ModelChoiceField(queryset=Proveedor.objects.none(), required=False, empty_label="—")
    tercero = forms.CharField(label="Otro tercero", max_length=150, required=False,
                              help_text="Si no es un cliente ni un proveedor registrado.")
    referencia = forms.CharField(max_length=60, required=False, help_text="N.º de comprobante, de depósito o de transferencia.")
    observaciones = forms.CharField(max_length=255, required=False)
    soporte = forms.FileField(required=False, help_text="Comprobante en PDF, JPG o PNG.")

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta"].queryset = _cuentas_de(empresa)
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa, activo=True)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(empresa=empresa, activo=True)
        self.fields["fecha"].initial = timezone.localdate()

    def clean_soporte(self):
        archivo = self.cleaned_data.get("soporte")
        if archivo:
            self.soporte_mime = validar_archivo(archivo)
        return archivo

    def clean(self):
        datos = super().clean()
        if datos.get("cliente") and datos.get("proveedor"):
            self.add_error("proveedor", "Elija un cliente o un proveedor, no los dos.")
        return datos


class TraspasoForm(Base, forms.Form):
    """Entre cuentas propias de la misma empresa: depositar la caja en el banco, pasar de ahorros a corriente…"""

    origen = forms.ModelChoiceField(label="Sale de", queryset=CuentaFinanciera.objects.none(), empty_label="Seleccione…")
    destino = forms.ModelChoiceField(label="Entra a", queryset=CuentaFinanciera.objects.none(), empty_label="Seleccione…")
    fecha = forms.DateField(widget=_FECHA)
    valor = forms.DecimalField(label="Valor ($)", **_VALOR)
    concepto = forms.CharField(max_length=200, initial="Depósito del efectivo de caja")
    referencia = forms.CharField(max_length=60, required=False, help_text="N.º de la papeleta de depósito o de la transferencia.")

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["origen"].queryset = self.fields["destino"].queryset = _cuentas_de(empresa)
        self.fields["fecha"].initial = timezone.localdate()

    def clean(self):
        datos = super().clean()
        if datos.get("origen") and datos.get("origen") == datos.get("destino"):
            self.add_error("destino", "Elija una cuenta distinta a la de origen.")
        return datos


class MotivoForm(Base, forms.Form):
    motivo = forms.CharField(label="Motivo", max_length=255)


# --------------------------------------------------------------------------- terceros
class ClienteForm(ControlConcurrencia, Base, forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["linea", "nombres", "tipo_identificacion", "identificacion", "telefono", "correo", "direccion",
                  "observaciones", "activo"]
        widgets = {"observaciones": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        if not self.instance.pk:
            del self.fields["activo"]

    def clean_nombres(self):
        return " ".join(self.cleaned_data["nombres"].split())

    def clean_telefono(self):
        return _telefono(self.cleaned_data.get("telefono"))

    def clean(self):
        datos = super().clean()
        if "identificacion" in datos and datos.get("tipo_identificacion"):
            try:
                datos["identificacion"] = validar_identificacion(datos["tipo_identificacion"], datos["identificacion"])
            except ValidationError as error:
                self.add_error("identificacion", error)
            else:
                otro = Cliente.objects.filter(empresa=self.empresa, identificacion=datos["identificacion"]).exclude(
                    pk=self.instance.pk).first()
                if otro:
                    self.add_error("identificacion", f"Ya está registrado: {otro}.")
        return datos


class ProveedorForm(ControlConcurrencia, Base, forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = ["nombre", "tipo", "tipo_identificacion", "identificacion", "contacto", "telefono", "correo",
                  "condicion_pago", "dias_credito", "observaciones", "activo"]
        widgets = {"observaciones": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        if not self.instance.pk:
            del self.fields["activo"]

    def clean_nombre(self):
        return " ".join(self.cleaned_data["nombre"].split())

    def clean_telefono(self):
        return _telefono(self.cleaned_data.get("telefono"))

    def clean(self):
        datos = super().clean()
        if datos.get("tipo_identificacion"):
            try:
                datos["identificacion"] = validar_identificacion(datos["tipo_identificacion"], datos.get("identificacion", ""),
                                                                 obligatoria=False)
            except ValidationError as error:
                self.add_error("identificacion", error)
            else:
                if datos["identificacion"] and Proveedor.objects.filter(
                        empresa=self.empresa, identificacion=datos["identificacion"]).exclude(pk=self.instance.pk).exists():
                    self.add_error("identificacion", "Ya hay un proveedor con esta identificación.")
        if datos.get("condicion_pago") == Proveedor.CondicionPago.CREDITO and not datos.get("dias_credito"):
            self.add_error("dias_credito", "Indique a cuántos días es el crédito.")
        if datos.get("condicion_pago") != Proveedor.CondicionPago.CREDITO:
            datos["dias_credito"] = None
        return datos


class LeadForm(ControlConcurrencia, Base, forms.ModelForm):
    class Meta:
        model = Lead
        fields = ["nombres", "telefono", "correo", "interes", "detalle", "origen", "responsable", "proximo_contacto", "notas"]
        widgets = {"proximo_contacto": _FECHA, "notas": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        responsables = get_user_model().objects.filter(is_active=True, groups__permissions__codename="change_lead").distinct()
        self.fields["responsable"].queryset = responsables.order_by("first_name", "last_name")
        self.fields["responsable"].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields["responsable"].empty_label = "Sin asignar"

    def clean_nombres(self):
        return " ".join(self.cleaned_data["nombres"].split())

    def clean_telefono(self):
        return _telefono(self.cleaned_data.get("telefono"))

    def clean(self):
        datos = super().clean()
        if not datos.get("telefono") and not datos.get("correo") and "telefono" not in self.errors:
            self.add_error("telefono", "Indique un teléfono o un correo para poder contactarlo.")
        return datos


class SeguimientoForm(Base, forms.Form):
    nota = forms.CharField(label="¿Qué pasó?", widget=forms.Textarea(attrs={"rows": 2}),
                           help_text="Ej.: se le envió la lista de requisitos por WhatsApp.")
    estado = forms.ChoiceField(label="Queda", choices=[(v, e) for v, e in Lead.Estado.choices if v != Lead.Estado.CONVERTIDO])
    proximo_contacto = forms.DateField(label="Próximo contacto", required=False, widget=_FECHA)
    motivo_perdida = forms.CharField(label="Por qué se perdió", max_length=255, required=False)


# --------------------------------------------------------------------------- libro diario
class AsientoForm(Base, forms.ModelForm):
    class Meta:
        model = Asiento
        fields = ["fecha", "tipo", "concepto", "documento"]
        widgets = {"fecha": _FECHA}

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha > timezone.localdate():
            raise ValidationError("La fecha no puede ser futura.")
        return fecha


class LineaForm(forms.Form):
    """Una fila del asiento. El tercero es un solo selector con clientes y proveedores («c:5», «p:3»)."""

    cuenta = forms.ModelChoiceField(queryset=CuentaContable.objects.none(), empty_label="Cuenta…")
    detalle = forms.CharField(max_length=255, required=False)
    tercero = forms.ChoiceField(required=False)
    debe = forms.DecimalField(min_value=Decimal("0"), max_digits=14, decimal_places=2, required=False)
    haber = forms.DecimalField(min_value=Decimal("0"), max_digits=14, decimal_places=2, required=False)

    def __init__(self, *args, cuentas, terceros, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta"].queryset = cuentas
        self.fields["cuenta"].label_from_instance = lambda c: f"{c.codigo} {c.nombre}"
        self.fields["tercero"].choices = terceros
        for nombre, campo in self.fields.items():
            campo.widget.attrs["class"] = "form-select form-select-sm" if isinstance(campo.widget, forms.Select) \
                else "form-control form-control-sm"
            campo.widget.attrs["aria-label"] = campo.label or nombre.capitalize()
        for nombre in ("debe", "haber"):
            self.fields[nombre].widget.attrs.update({"class": "form-control form-control-sm text-end", "step": "0.01",
                                                     "inputmode": "decimal", "data-columna": nombre})

    def clean(self):
        datos = super().clean()
        debe, haber = datos.get("debe") or Decimal("0"), datos.get("haber") or Decimal("0")
        if not datos.get("cuenta"):
            return datos
        if debe and haber:
            raise ValidationError("Cada línea va al debe o al haber, no a los dos.")
        if not debe and not haber:
            raise ValidationError("Ponga el valor al debe o al haber.")
        datos["debe"], datos["haber"] = debe, haber
        return datos

    def vacia(self) -> bool:
        return not self.cleaned_data.get("cuenta") and not self.cleaned_data.get("debe") and not self.cleaned_data.get("haber")


class LineaFormSetBase(forms.BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        lineas = [f for f in self.forms if f.cleaned_data and not f.vacia()]
        for f in self.forms:
            if f.cleaned_data and not f.cleaned_data.get("cuenta") and not f.vacia():
                raise ValidationError("Hay una línea con valor pero sin cuenta.")
        if len(lineas) < 2:
            raise ValidationError("El asiento necesita al menos dos líneas.")

    def lineas(self, empresa) -> list[dict]:
        resultado = []
        for f in self.forms:
            if not f.cleaned_data or f.vacia():
                continue
            d = f.cleaned_data
            tercero, cliente, proveedor = d.get("tercero") or "", None, None
            if tercero.startswith("c:"):
                cliente = Cliente.objects.filter(empresa=empresa, pk=tercero[2:]).first()
            elif tercero.startswith("p:"):
                proveedor = Proveedor.objects.filter(empresa=empresa, pk=tercero[2:]).first()
            resultado.append({"cuenta": d["cuenta"], "detalle": d.get("detalle", "").strip(), "cliente": cliente,
                              "proveedor": proveedor, "debe": d["debe"], "haber": d["haber"]})
        return resultado


LineaFormSet = forms.formset_factory(LineaForm, formset=LineaFormSetBase, extra=0, min_num=2, max_num=200, validate_max=True)


def terceros_de(empresa) -> list[tuple]:
    clientes = [(f"c:{c.pk}", c.nombres) for c in Cliente.objects.filter(empresa=empresa, activo=True).order_by("nombres")]
    proveedores = [(f"p:{p.pk}", p.nombre) for p in Proveedor.objects.filter(empresa=empresa, activo=True).order_by("nombre")]
    opciones = [("", "—")]
    if clientes:
        opciones.append(("Clientes", clientes))
    if proveedores:
        opciones.append(("Proveedores", proveedores))
    return opciones


def iniciales_de_lineas(asiento: Asiento) -> list[dict]:
    return [{"cuenta": l.cuenta_id, "detalle": l.detalle, "debe": l.debe or None, "haber": l.haber or None,
             "tercero": f"c:{l.cliente_id}" if l.cliente_id else (f"p:{l.proveedor_id}" if l.proveedor_id else "")}
            for l in asiento.lineas.all()]


# --------------------------------------------------------------------------- activos fijos
class ActivoFijoForm(ControlConcurrencia, Base, forms.ModelForm):
    class Meta:
        model = ActivoFijo
        fields = ["descripcion", "codigo", "cuenta_contable", "cantidad", "valor_unitario", "fecha_compra", "proveedor",
                  "factura", "ubicacion", "custodio", "vida_util_anios", "estado", "observaciones"]
        widgets = {"fecha_compra": _FECHA, "observaciones": forms.Textarea(attrs={"rows": 2})}
        help_texts = {"codigo": "Déjelo vacío para generarlo (AF-0001…).",
                      "vida_util_anios": "Referencia: muebles 10, equipos de oficina 10, computación 3, vehículos 5."}

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields["codigo"].required = False
        self.fields["cuenta_contable"].queryset = servicios.cuentas_de_movimiento(codigo__startswith="1.2.01")
        self.fields["proveedor"].queryset = Proveedor.objects.filter(empresa=empresa)

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip().upper()
        if codigo and ActivoFijo.objects.filter(empresa=self.empresa, codigo=codigo).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Ese código ya está en uso.")
        return codigo
