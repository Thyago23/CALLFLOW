import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.concurrencia import ControlConcurrencia
from apps.core.forms import FormularioEstilizado, _estilizar
from apps.personal.models import Empleado

from apps.documentos.archivos import validar_archivo

from .models import ActaEquipo, Equipo, EventoEquipo, Licencia, PlanMantenimiento, Ticket

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


def _casillas(form):
    """_estilizar pone form-control a todo; las casillas usan form-check-input."""
    for campo in form.fields.values():
        if isinstance(campo.widget, forms.CheckboxInput):
            campo.widget.attrs["class"] = "form-check-input"


# --------------------------------------------------------------------------- mesa de ayuda
class TicketForm(FormularioEstilizado, forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ["categoria", "titulo", "descripcion", "prioridad"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 4})}
        labels = {"prioridad": "¿Cuánto le afecta?"}

    def __init__(self, *args, es_ti=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not es_ti:  # la prioridad crítica (afecta a varias personas) la pone TI
            self.fields["prioridad"].choices = [c for c in Ticket.Prioridad.choices if c[0] != Ticket.Prioridad.CRITICA]
        _estilizar(self)
        self.fields["titulo"].widget.attrs["placeholder"] = "Ej.: No tengo audio en la diadema"
        self.fields["descripcion"].widget.attrs["placeholder"] = "Desde cuándo pasa, qué intentó, si le pasa a alguien más…"

    def clean_titulo(self):
        return (self.cleaned_data.get("titulo") or "").strip()


class ComentarioTicketForm(FormularioEstilizado, forms.Form):
    texto = forms.CharField(label="Mensaje", max_length=4000, widget=forms.Textarea(attrs={"rows": 3}))
    interno = forms.BooleanField(label="Nota interna (solo la ve TI)", required=False)

    def __init__(self, *args, es_ti=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not es_ti:
            del self.fields["interno"]
        _estilizar(self)
        _casillas(self)


class GestionTicketForm(FormularioEstilizado, forms.Form):
    estado = forms.ChoiceField(choices=Ticket.Estado.choices)
    prioridad = forms.ChoiceField(choices=Ticket.Prioridad.choices)
    asignado_a = forms.ModelChoiceField(label="Atiende", queryset=None, required=False, empty_label="Sin asignar")
    equipo = forms.ModelChoiceField(queryset=Equipo.objects.exclude(estado=Equipo.Estado.BAJA), required=False,
                                    empty_label="Sin equipo")
    solucion = forms.CharField(label="Solución", required=False, max_length=4000, widget=forms.Textarea(attrs={"rows": 3}),
                               help_text="Obligatoria para marcarlo como resuelto. La ve quien reportó.")
    nota = forms.CharField(label="Mensaje para quien reportó (opcional)", required=False, max_length=4000,
                           widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        from apps.core.models import Rol, Usuario

        super().__init__(*args, **kwargs)
        self.fields["asignado_a"].queryset = Usuario.objects.filter(rol=Rol.TI, is_active=True).order_by("first_name")
        self.fields["asignado_a"].label_from_instance = lambda u: u.get_full_name() or u.username
        _estilizar(self)

    def clean(self):
        datos = super().clean()
        if datos.get("estado") in (Ticket.Estado.RESUELTO, Ticket.Estado.CERRADO) and not (datos.get("solucion") or "").strip():
            self.add_error("solucion", "Escriba qué se hizo para resolverlo.")
        return datos


class ConfirmarTicketForm(forms.Form):
    solucionado = forms.TypedChoiceField(choices=[("si", "Sí"), ("no", "No")], coerce=lambda v: v == "si")
    texto = forms.CharField(required=False, max_length=4000)


# --------------------------------------------------------------------------- licencias y servicios
class LicenciaForm(ControlConcurrencia, FormularioEstilizado, forms.ModelForm):
    class Meta:
        model = Licencia
        fields = ["tipo", "nombre", "proveedor", "referencia", "cantidad", "fecha_inicio", "fecha_vencimiento", "dias_aviso",
                  "renovacion_automatica", "costo", "activa", "equipos", "observacion"]
        widgets = {
            "fecha_inicio": forms.DateInput(attrs=FECHA, format="%Y-%m-%d"),
            "fecha_vencimiento": forms.DateInput(attrs=FECHA, format="%Y-%m-%d"),
            "observacion": forms.Textarea(attrs={"rows": 2}),
            "equipos": forms.SelectMultiple(attrs={"size": 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipos"].queryset = Equipo.objects.exclude(estado=Equipo.Estado.BAJA).order_by("codigo")
        self.fields["equipos"].help_text = "Opcional. Ctrl + clic para elegir varios."
        _estilizar(self)
        self.fields["equipos"].widget.attrs["class"] = "form-select"
        _casillas(self)

    def clean_referencia(self):
        valor = (self.cleaned_data.get("referencia") or "").strip()
        # Una clave de producto completa (XXXXX-XXXXX-XXXXX-XXXXX-XXXXX) no se guarda en el sistema.
        if re.fullmatch(r"(?:[A-Z0-9]{5}-){4}[A-Z0-9]{5}", valor.upper()):
            raise ValidationError("No guarde la clave completa: escriba solo los últimos 5 caracteres o el n.º de contrato.")
        return valor

    def clean(self):
        datos = super().clean()
        inicio, vence = datos.get("fecha_inicio"), datos.get("fecha_vencimiento")
        if inicio and vence and vence < inicio:
            self.add_error("fecha_vencimiento", "El vencimiento no puede ser antes del inicio.")
        if (datos.get("dias_aviso") or 0) > 365:
            self.add_error("dias_aviso", "Como máximo 365 días.")
        return datos


# --------------------------------------------------------------------------- mantenimiento preventivo
class PlanNuevoForm(FormularioEstilizado, forms.Form):
    """Un plan para un equipo o, de una vez, para todos los equipos de un tipo."""

    equipo = forms.ModelChoiceField(queryset=Equipo.objects.exclude(estado=Equipo.Estado.BAJA).order_by("codigo"),
                                    required=False, empty_label="— Todos los equipos del tipo elegido —")
    tipo = forms.ChoiceField(label="Tipo de equipo", choices=[("", "—")] + list(Equipo.Tipo.choices), required=False,
                             help_text="Para crear el plan en todos los equipos de ese tipo (menos los dados de baja).")
    actividad = forms.CharField(max_length=100, initial="Limpieza interna y externa")
    cada_meses = forms.IntegerField(label="Cada (meses)", min_value=1, max_value=36, initial=6)
    primera_fecha = forms.DateField(label="Primera fecha", widget=forms.DateInput(attrs=FECHA))
    dias_aviso = forms.IntegerField(label="Avisar con (días)", min_value=0, max_value=90, initial=7)
    observacion = forms.CharField(label="Observación", max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)

    def clean(self):
        datos = super().clean()
        if not datos.get("equipo") and not datos.get("tipo"):
            raise ValidationError("Elija un equipo o un tipo de equipo.")
        datos["actividad"] = (datos.get("actividad") or "").strip()
        return datos

    def equipos(self):
        if self.cleaned_data.get("equipo"):
            return [self.cleaned_data["equipo"]]
        return list(Equipo.objects.filter(tipo=self.cleaned_data["tipo"]).exclude(estado=Equipo.Estado.BAJA).order_by("codigo"))


class PlanForm(FormularioEstilizado, forms.ModelForm):
    class Meta:
        model = PlanMantenimiento
        fields = ["actividad", "cada_meses", "proxima_fecha", "dias_aviso", "activo", "observacion"]
        widgets = {"proxima_fecha": forms.DateInput(attrs=FECHA, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)
        _casillas(self)

    def clean(self):
        datos = super().clean()
        cada = datos.get("cada_meses")
        if cada is not None and not 1 <= cada <= 36:
            self.add_error("cada_meses", "Entre 1 y 36 meses.")
        if (datos.get("dias_aviso") or 0) > 90:
            self.add_error("dias_aviso", "Como máximo 90 días.")
        proxima, ultima = datos.get("proxima_fecha"), self.instance.ultima_fecha
        if proxima and ultima and proxima <= ultima:
            self.add_error("proxima_fecha", f"Debe ser después del último mantenimiento ({ultima:%d/%m/%Y}).")
        actividad = (datos.get("actividad") or "").strip()
        if actividad and PlanMantenimiento.objects.filter(equipo_id=self.instance.equipo_id, actividad__iexact=actividad
                                                          ).exclude(pk=self.instance.pk).exists():
            self.add_error("actividad", "Este equipo ya tiene un plan con esa actividad.")
        return datos


class MantenimientoRealizadoForm(FormularioEstilizado, forms.Form):
    fecha = forms.DateField(widget=forms.DateInput(attrs=FECHA))
    descripcion = forms.CharField(label="Qué se hizo (opcional)", required=False, max_length=2000,
                                  widget=forms.Textarea(attrs={"rows": 3}))
    realizado_por = forms.CharField(label="Realizado por", required=False, max_length=100)
    costo = forms.DecimalField(label="Costo ($)", required=False, min_value=0, max_digits=10, decimal_places=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha > timezone.localdate():
            raise ValidationError("La fecha no puede ser futura.")
        return fecha
