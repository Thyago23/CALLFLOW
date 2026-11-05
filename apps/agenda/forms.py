from datetime import datetime, time

from django import forms
from django.utils import timezone

from apps.core.forms import FormularioEstilizado, _estilizar
from apps.core.models import Usuario

from .models import Evento
from .servicios import OPCIONES_AVISO


class EventoForm(FormularioEstilizado, forms.Form):
    """SCRUM-28 · Actividades, reuniones y recordatorios."""

    titulo = forms.CharField(label="Título", max_length=150)
    tipo = forms.ChoiceField(label="Tipo", choices=Evento.Tipo.choices, widget=forms.RadioSelect)
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    todo_el_dia = forms.BooleanField(label="Todo el día", required=False)
    hora_inicio = forms.TimeField(label="Desde", required=False, widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    hora_fin = forms.TimeField(label="Hasta", required=False, widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    ubicacion = forms.CharField(label="Lugar", max_length=150, required=False,
                                widget=forms.TextInput(attrs={"placeholder": "Ej.: Sala de reuniones, Zoom"}))
    minutos_aviso = forms.TypedChoiceField(label="Avisarme", choices=OPCIONES_AVISO, coerce=int, initial=15)
    participantes = forms.ModelMultipleChoiceField(
        label="Invitar a", required=False, queryset=Usuario.objects.none(), widget=forms.CheckboxSelectMultiple,
        help_text="Verán el evento en su agenda y recibirán el aviso.",
    )
    descripcion = forms.CharField(label="Notas", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        qs = Usuario.objects.filter(is_active=True).order_by("first_name", "last_name")
        self.fields["participantes"].queryset = qs.exclude(pk=usuario.pk) if usuario else qs
        self.fields["participantes"].label_from_instance = lambda u: u.get_full_name() or u.username
        _estilizar(self)
        self.fields["tipo"].widget.attrs["class"] = "btn-check"
        self.fields["todo_el_dia"].widget.attrs["class"] = "form-check-input"
        self.fields["participantes"].widget.attrs["class"] = "form-check-input"

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
        }
        return cls(initial=inicial, **kwargs)

    def clean(self):
        datos = super().clean()
        todo_el_dia, inicio, fin = datos.get("todo_el_dia"), datos.get("hora_inicio"), datos.get("hora_fin")
        if not todo_el_dia:
            if inicio is None:
                self.add_error("hora_inicio", "Indique la hora (o marque «Todo el día»).")
            elif fin is not None and fin <= inicio:
                self.add_error("hora_fin", "Debe ser posterior a la hora de inicio.")
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
        if evento.todo_el_dia:
            evento.inicio, evento.fin = timezone.make_aware(datetime.combine(d["fecha"], time.min), zona), None
        else:
            evento.inicio = timezone.make_aware(datetime.combine(d["fecha"], d["hora_inicio"]), zona)
            evento.fin = timezone.make_aware(datetime.combine(d["fecha"], d["hora_fin"]), zona) if d["hora_fin"] else None
        return evento
