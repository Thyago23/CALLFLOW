import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.concurrencia import ControlConcurrencia
from apps.core.forms import FormularioEstilizado, _estilizar
from apps.personal.models import Empleado

from apps.documentos.archivos import validar_archivo

from .models import ActaEquipo, Equipo, EventoEquipo

FECHA = {"type": "date"}


class EquipoForm(ControlConcurrencia, FormularioEstilizado, forms.ModelForm):
    class Meta:
        model = Equipo
        fields = ["codigo", "tipo", "marca", "modelo", "numero_serie", "procesador", "memoria_ram_gb", "almacenamiento",
                  "sistema_operativo", "hostname", "direccion_ip", "direccion_mac", "ubicacion", "estado",
                  "fecha_compra", "proveedor", "costo", "garantia_hasta", "observacion"]
        widgets = {
            "fecha_compra": forms.DateInput(attrs=FECHA, format="%Y-%m-%d"),
            "garantia_hasta": forms.DateInput(attrs=FECHA, format="%Y-%m-%d"),
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["codigo"].required = False
        _estilizar(self)
        self.fields["codigo"].widget.attrs["placeholder"] = "Automático (EQ-0001)"
        self.fields["direccion_ip"].widget.attrs["placeholder"] = "192.168.1.25"
        self.fields["direccion_mac"].widget.attrs["placeholder"] = "AA:BB:CC:DD:EE:FF"
        if self.instance.pk:
            self.fields["estado"].help_text = ("Los cambios de estado quedan solos en la hoja de vida. Para entregar o recibir "
                                               "el equipo use «Entregar» o «Recibir devolución»: así se emite el acta.")

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
        if self.instance.asignado_a_id and datos.get("estado") in (Equipo.Estado.BAJA, Equipo.Estado.BODEGA):
            self.add_error("estado", f"El equipo está entregado a {self.instance.asignado_a}: registre primero la devolución.")
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


class _ActaForm(FormularioEstilizado, forms.Form):
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs=FECHA, format="%Y-%m-%d"))
    condicion = forms.ChoiceField(label="Estado físico del equipo", choices=ActaEquipo.Condicion.choices)
    accesorios = forms.CharField(label="Accesorios", max_length=255, required=False,
                                 help_text="Lo que se entrega junto con el equipo: cargador, mouse, maletín…")
    observacion = forms.CharField(label="Observaciones", required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                  help_text="Rayones, golpes, piezas faltantes o cualquier detalle que deba constar en el acta.")

    def __init__(self, *args, equipo, **kwargs):
        kwargs.setdefault("initial", {}).setdefault("fecha", timezone.localdate())
        super().__init__(*args, **kwargs)
        self.equipo = equipo
        _estilizar(self)
        self.fields["accesorios"].widget.attrs["placeholder"] = "Cargador, mouse, maletín"

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha > timezone.localdate():
            raise ValidationError("La fecha no puede ser futura.")
        return fecha

    def clean_accesorios(self):
        return " ".join(self.cleaned_data.get("accesorios", "").split())

    def clean_observacion(self):
        return self.cleaned_data.get("observacion", "").strip()


class EntregaForm(_ActaForm):
    """Entrega de un equipo (asignación o préstamo). Genera el acta de entrega."""

    field_order = ["empleado", "modalidad", "devolucion_prevista", "fecha", "condicion", "accesorios", "observacion"]
    empleado = forms.ModelChoiceField(queryset=Empleado.objects.none(), label="Entregar a", empty_label="Seleccione un empleado")
    modalidad = forms.ChoiceField(label="Modalidad", choices=ActaEquipo.Modalidad.choices, initial=ActaEquipo.Modalidad.ASIGNACION,
                                  widget=forms.RadioSelect)
    devolucion_prevista = forms.DateField(label="Devolver hasta", required=False,
                                          widget=forms.DateInput(attrs=FECHA, format="%Y-%m-%d"),
                                          help_text="Obligatoria en los préstamos.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empleado"].queryset = (Empleado.objects.exclude(estado=Empleado.Estado.DESVINCULADO)
                                            .select_related("cargo").order_by("apellidos", "nombres"))
        self.fields["empleado"].label_from_instance = lambda e: f"{e.apellidos} {e.nombres} · {e.cargo}"
        self.fields["modalidad"].widget.attrs.pop("class", None)

    def clean(self):
        datos = super().clean()
        fecha, hasta = datos.get("fecha"), datos.get("devolucion_prevista")
        if datos.get("modalidad") == ActaEquipo.Modalidad.PRESTAMO:
            if not hasta:
                self.add_error("devolucion_prevista", "Indique hasta cuándo se presta el equipo.")
            elif fecha and hasta < fecha:
                self.add_error("devolucion_prevista", "La devolución no puede ser antes de la entrega.")
        else:
            datos["devolucion_prevista"] = None
        return datos


class DevolucionForm(_ActaForm):
    """Devolución de un equipo por quien lo tiene. Genera el acta de devolución."""

    ESTADOS = [(Equipo.Estado.BODEGA.value, "Queda en bodega (disponible)"),
               (Equipo.Estado.REPARACION.value, "Pasa a reparación"),
               (Equipo.Estado.OPERATIVO.value, "Sigue operativo en su puesto")]
    estado_destino = forms.ChoiceField(label="Después de recibirlo, el equipo…", choices=ESTADOS, initial=Equipo.Estado.BODEGA)

    def __init__(self, *args, entrega=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.entrega = entrega
        if entrega:
            self.fields["accesorios"].initial = entrega.accesorios
            self.fields["accesorios"].help_text = "Los que se devuelven. Si falta alguno de los entregados, anótelo en las observaciones."

    def clean(self):
        datos = super().clean()
        fecha = datos.get("fecha")
        if self.entrega and fecha and fecha < self.entrega.fecha:
            self.add_error("fecha", f"La devolución no puede ser antes de la entrega ({self.entrega.fecha:%d/%m/%Y}).")
        if datos.get("condicion") == ActaEquipo.Condicion.DANADO and not datos.get("observacion"):
            self.add_error("observacion", "Describa los daños: quedan en el acta.")
        return datos


class ActaFirmadaForm(forms.Form):
    archivo = forms.FileField(label="Acta firmada (PDF, JPG o PNG, máx. 10 MB)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["archivo"].widget.attrs.update({"class": "form-control", "accept": ".pdf,.jpg,.jpeg,.png"})

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        self.mime = validar_archivo(archivo)
        return archivo
