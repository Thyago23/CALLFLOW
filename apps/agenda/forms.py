from datetime import datetime, time

from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.core import jornada
from apps.core.forms import FormularioEstilizado, _estilizar
from apps.core.models import Usuario
from apps.ventas.models import Cliente

from .models import Evento
from .servicios import OPCIONES_AVISO, clientes_agendables
from .widgets import HoraLaboralWidget


class EventoForm(FormularioEstilizado, forms.Form):
    """SCRUM-28 · Actividades, reuniones y recordatorios."""

    titulo = forms.CharField(label="Título", max_length=150)
    tipo = forms.ChoiceField(label="Tipo", choices=Evento.Tipo.choices, widget=forms.RadioSelect)
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    todo_el_dia = forms.BooleanField(label="Todo el día", required=False)
    # Solo se ofrecen horas de la jornada laboral (el selector del navegador muestra las 24 horas).
    hora_inicio = forms.TimeField(label="Desde", required=False, widget=HoraLaboralWidget())
    hora_fin = forms.TimeField(label="Hasta", required=False, widget=HoraLaboralWidget(incluir_cierre=True))
    ubicacion = forms.CharField(label="Lugar", max_length=150, required=False,
                                widget=forms.TextInput(attrs={"placeholder": "Ej.: Sala de reuniones, Zoom"}))
    minutos_aviso = forms.TypedChoiceField(label="Avisarme", choices=OPCIONES_AVISO, coerce=int, initial=15)
    participantes = forms.ModelMultipleChoiceField(
        label="Invitar a", required=False, queryset=Usuario.objects.none(), widget=forms.CheckboxSelectMultiple,
        help_text="Verán el evento en su agenda y recibirán el aviso.",
    )
    descripcion = forms.CharField(label="Notas", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), required=False, widget=forms.HiddenInput)

    def __init__(self, *args, usuario=None, cliente_actual=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        # Solo clientes que la persona puede ver (más el que ya tenía el evento, al editarlo).
        permitidos = Q(pk__in=clientes_agendables(usuario).values("pk")) if usuario else Q(pk__in=[])
        if cliente_actual is not None:
            permitidos |= Q(pk=cliente_actual.pk)
        self.fields["cliente"].queryset = Cliente.objects.filter(permitidos)
        qs = Usuario.objects.filter(is_active=True).order_by("first_name", "last_name")
        self.fields["participantes"].queryset = qs.exclude(pk=usuario.pk) if usuario else qs
        self.fields["participantes"].label_from_instance = lambda u: u.get_full_name() or u.username
        _estilizar(self)
        self.fields["tipo"].widget.attrs["class"] = "btn-check"
        self.fields["todo_el_dia"].widget.attrs["class"] = "form-check-input"
        self.fields["participantes"].widget.attrs["class"] = "form-check-input"
        self.fields["fecha"].help_text = f"Horario laboral: {jornada.descripcion()}."

    @classmethod
    def desde_evento(cls, evento: Evento, **kwargs):
        inicio = timezone.localtime(evento.inicio)
        fin = timezone.localtime(evento.fin) if evento.fin else None
        inicial = {
            "titulo": evento.titulo, "tipo": evento.tipo, "fecha": inicio.date(), "todo_el_dia": evento.todo_el_dia,
            "hora_inicio": None if evento.todo_el_dia else inicio.time(),
            "hora_fin": fin.time() if fin and not evento.todo_el_dia else None,
            "ubicacion": evento.ubicacion, "minutos_aviso": evento.minutos_aviso, "descripcion": evento.descripcion,
            "participantes": list(evento.participantes.values_list("usuario_id", flat=True)),
            "cliente": evento.cliente_id,
        }
        return cls(initial=inicial, cliente_actual=evento.cliente, **kwargs)

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if not jornada.es_laborable(fecha):
            raise forms.ValidationError(f"Ese día no es laborable. Solo se agenda {jornada.texto_dias()}.")
        return fecha

    def clean(self):
        datos = super().clean()
        todo_el_dia, inicio, fin = datos.get("todo_el_dia"), datos.get("hora_inicio"), datos.get("hora_fin")
        apertura, cierre = jornada.inicio(), jornada.fin()
        if not todo_el_dia:
            if inicio is None:
                self.add_error("hora_inicio", "Indique la hora (o marque «Todo el día»).")
            elif not (apertura <= inicio < cierre):
                self.add_error("hora_inicio", f"Fuera del horario laboral: debe ser desde las {apertura:%H:%M} y antes de las {cierre:%H:%M}.")
            elif fin is not None and fin <= inicio:
                self.add_error("hora_fin", "Debe ser posterior a la hora de inicio.")
            elif fin is not None and fin > cierre:
                self.add_error("hora_fin", f"Fuera del horario laboral: debe terminar a más tardar a las {cierre:%H:%M}.")
        return datos

    def aplicar(self, evento: Evento) -> Evento:
        d = self.cleaned_data
        zona = timezone.get_current_timezone()
        evento.titulo = " ".join(d["titulo"].split())
        evento.tipo = d["tipo"]
        evento.todo_el_dia = d["todo_el_dia"]
        evento.ubicacion = d["ubicacion"].strip()
        evento.descripcion = d["descripcion"].strip()
        evento.minutos_aviso = d["minutos_aviso"]
        evento.cliente = d.get("cliente")
        if evento.todo_el_dia:
            evento.inicio, evento.fin = timezone.make_aware(datetime.combine(d["fecha"], time.min), zona), None
        else:
            evento.inicio = timezone.make_aware(datetime.combine(d["fecha"], d["hora_inicio"]), zona)
            evento.fin = timezone.make_aware(datetime.combine(d["fecha"], d["hora_fin"]), zona) if d["hora_fin"] else None
        return evento
