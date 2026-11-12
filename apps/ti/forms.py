import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.forms import FormularioEstilizado, _estilizar
from apps.personal.models import Empleado

from .models import Equipo, EventoEquipo

FECHA = {"type": "date"}


class EquipoForm(FormularioEstilizado, forms.ModelForm):
    class Meta:
        model = Equipo
        fields = ["codigo", "tipo", "marca", "modelo", "numero_serie", "procesador", "memoria_ram_gb", "almacenamiento",
                  "sistema_operativo", "hostname", "direccion_ip", "direccion_mac", "ubicacion", "asignado_a", "estado",
                  "fecha_compra", "proveedor", "costo", "garantia_hasta", "observacion"]
        widgets = {
            "fecha_compra": forms.DateInput(attrs=FECHA, format="%Y-%m-%d"),
            "garantia_hasta": forms.DateInput(attrs=FECHA, format="%Y-%m-%d"),
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["codigo"].required = False
        self.fields["asignado_a"].queryset = (Empleado.objects.exclude(estado=Empleado.Estado.DESVINCULADO)
                                              .order_by("apellidos", "nombres"))
        self.fields["asignado_a"].empty_label = "Sin asignar"
        _estilizar(self)
        self.fields["codigo"].widget.attrs["placeholder"] = "Automático (EQ-0001)"
        self.fields["direccion_ip"].widget.attrs["placeholder"] = "192.168.1.25"
        self.fields["direccion_mac"].widget.attrs["placeholder"] = "AA:BB:CC:DD:EE:FF"
        if self.instance.pk:
            self.fields["estado"].help_text = "Los cambios de estado y de asignación quedan solos en la hoja de vida del equipo."

    def clean_codigo(self):
        return (self.cleaned_data.get("codigo") or "").strip().upper()

    def clean_numero_serie(self):
        serie = (self.cleaned_data.get("numero_serie") or "").strip().upper()
        otro = Equipo.objects.filter(numero_serie=serie).exclude(pk=self.instance.pk).first() if serie else None
        if otro:
            raise ValidationError(f"Esa serie ya está registrada en el equipo {otro.codigo}.")
        return serie

    def clean_direccion_mac(self):
        valor = (self.cleaned_data.get("direccion_mac") or "").strip()
        if not valor:
            return ""
        digitos = re.sub(r"[^0-9A-Fa-f]", "", valor)
        if len(digitos) != 12:
            raise ValidationError("Escriba la MAC con 12 caracteres hexadecimales, ej. AA:BB:CC:DD:EE:FF.")
        return ":".join(digitos[i:i + 2] for i in range(0, 12, 2)).upper()

    def clean_hostname(self):
        valor = (self.cleaned_data.get("hostname") or "").strip()
        if valor and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", valor):
            raise ValidationError("Use solo letras, números, puntos y guiones (ej. CALL-PC-07).")
        return valor.upper()

    def clean(self):
        datos = super().clean()
        compra, garantia = datos.get("fecha_compra"), datos.get("garantia_hasta")
        if compra and compra > timezone.localdate():
            self.add_error("fecha_compra", "La fecha de compra no puede ser futura.")
        if compra and garantia and garantia < compra:
            self.add_error("garantia_hasta", "La garantía no puede terminar antes de la compra.")
        if datos.get("estado") == Equipo.Estado.BAJA and datos.get("asignado_a"):
            self.add_error("asignado_a", "Un equipo dado de baja no puede quedar asignado a nadie.")
        return datos


class EventoForm(FormularioEstilizado, forms.ModelForm):
    """Eventos que se registran a mano (las asignaciones, devoluciones y cambios de estado se anotan solos)."""

    TIPOS_MANUALES = [EventoEquipo.Tipo.MANTENIMIENTO, EventoEquipo.Tipo.REPARACION, EventoEquipo.Tipo.ACTUALIZACION,
                      EventoEquipo.Tipo.OTRO]

    class Meta:
        model = EventoEquipo
        fields = ["fecha", "tipo", "descripcion", "realizado_por", "costo"]
        widgets = {"fecha": forms.DateInput(attrs=FECHA, format="%Y-%m-%d"), "descripcion": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("initial", {"fecha": timezone.localdate()})
        super().__init__(*args, **kwargs)
        self.fields["tipo"].choices = [(t.value, t.label) for t in self.TIPOS_MANUALES]
        _estilizar(self)
        self.fields["descripcion"].widget.attrs["placeholder"] = "Qué se hizo, qué se cambió, qué piezas se usaron…"

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha > timezone.localdate():
            raise ValidationError("La fecha no puede ser futura.")
        return fecha
