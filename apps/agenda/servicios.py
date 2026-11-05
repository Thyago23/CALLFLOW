"""
Agenda (SCRUM-28 y SCRUM-29).

- Cada persona ve sus eventos y aquellos a los que fue invitada; solo quien lo creó lo edita.
- Avisos: se notifica `minutos_aviso` antes del inicio (en los eventos de todo el día, antes
  del inicio de la jornada laboral) al dueño y a los participantes, una sola vez.
  No hay un proceso en segundo plano: los avisos se generan mientras las personas usan el
  sistema (middleware, como máximo una vez por minuto) y con el comando
  `python manage.py enviar_avisos_agenda`, que puede programarse cada minuto.
"""
import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from apps.core import jornada
from apps.core.models import Notificacion

from .models import Evento, ParticipanteEvento

MAX_AVISO = timedelta(minutes=10080)  # una semana, igual que la restricción de la tabla

OPCIONES_AVISO = [
    (0, "A la hora del evento"),
    (5, "5 minutos antes"),
    (15, "15 minutos antes"),
    (30, "30 minutos antes"),
    (60, "1 hora antes"),
    (1440, "1 día antes"),
]


# --------------------------------------------------------------------------- visibilidad
def eventos_de(usuario):
    """Eventos propios y aquellos en los que la persona es participante."""
    return (
        Evento.objects.filter(Q(propietario=usuario) | Q(participantes__usuario=usuario))
        .distinct()
        .select_related("propietario")
    )


def puede_editar(usuario, evento) -> bool:
    return evento.propietario_id == usuario.pk and usuario.has_perm("agenda.change_evento")


# --------------------------------------------------------------------------- avisos (SCRUM-29)
def momento_aviso(evento: Evento) -> datetime:
    base = evento.inicio
    if evento.todo_el_dia:
        dia = timezone.localtime(evento.inicio).date()
        base = timezone.make_aware(datetime.combine(dia, jornada.inicio()), timezone.get_current_timezone())
    return base - timedelta(minutes=evento.minutos_aviso)


def ya_empezo(evento: Evento, ahora: datetime | None = None) -> bool:
    ahora = ahora or timezone.now()
    if evento.todo_el_dia:
        return timezone.localtime(evento.inicio).date() < timezone.localtime(ahora).date()
    return evento.inicio < ahora


def preparar_aviso(evento: Evento) -> None:
    """
    Al crear o reprogramar: se avisa salvo que el evento ya haya empezado. Si el momento del
    aviso ya llegó (ej.: evento en 10 minutos con aviso de 15), el aviso sale de inmediato.
    """
    evento.aviso_enviado = evento.estado != Evento.Estado.PENDIENTE or ya_empezo(evento)


def _texto_cuando(evento: Evento, ahora: datetime) -> str:
    inicio = timezone.localtime(evento.inicio)
    hoy = timezone.localtime(ahora).date()
    if inicio.date() == hoy:
        dia = "Hoy"
    elif inicio.date() == hoy + timedelta(days=1):
        dia = "Mañana"
    else:
        dia = date_format(inicio.date(), "l j \\d\\e F").capitalize()
    texto = dia if evento.todo_el_dia else f"{dia} a las {inicio:%H:%M}"
    return f"{texto} · {evento.ubicacion}" if evento.ubicacion else texto


@transaction.atomic
def generar_avisos(ahora: datetime | None = None, usuario=None) -> int:
    """Crea las notificaciones de los eventos cuyo momento de aviso ya llegó. Devuelve cuántos eventos avisó."""
    ahora = ahora or timezone.now()
    candidatos = Evento.objects.filter(
        estado=Evento.Estado.PENDIENTE, aviso_enviado=False,
        inicio__lte=ahora + MAX_AVISO + timedelta(days=1), inicio__gte=ahora - timedelta(days=1),
    )
    if usuario is not None:
        candidatos = candidatos.filter(Q(propietario=usuario) | Q(participantes__usuario=usuario))
    # skip_locked: si dos peticiones revisan a la vez, cada evento se avisa una sola vez.
    candidatos = Evento.objects.select_for_update(skip_locked=True).filter(pk__in=candidatos.values("pk"))

    avisados = 0
    for evento in candidatos:
        if momento_aviso(evento) > ahora:
            continue
        destinatarios = {evento.propietario_id, *evento.participantes.values_list("usuario_id", flat=True)}
        Notificacion.objects.bulk_create([
            Notificacion(
                destinatario_id=uid, tipo=Notificacion.Tipo.AGENDA,
                titulo=f"{evento.get_tipo_display()}: {evento.titulo}"[:150],
                mensaje=_texto_cuando(evento, ahora),
                url=reverse("agenda:detalle", args=[evento.pk]),
            )
            for uid in destinatarios
        ])
        evento.aviso_enviado = True
        evento.save(update_fields=["aviso_enviado", "actualizado_en"])
        avisados += 1
    return avisados


def vencidos(usuario, ahora: datetime | None = None):
    """Pendientes cuya fecha ya pasó (no se completaron ni cancelaron)."""
    ahora = ahora or timezone.now()
    hoy = timezone.localtime(ahora).date()
    return eventos_de(usuario).filter(estado=Evento.Estado.PENDIENTE).filter(
        Q(todo_el_dia=False, fin__isnull=False, fin__lt=ahora)
        | Q(todo_el_dia=False, fin__isnull=True, inicio__lt=ahora)
        | Q(todo_el_dia=True, inicio__date__lt=hoy)
    ).order_by("-inicio")


def proximos(usuario, dias: int = 7, ahora: datetime | None = None):
    ahora = ahora or timezone.now()
    return eventos_de(usuario).filter(
        estado=Evento.Estado.PENDIENTE, inicio__gte=ahora, inicio__lt=ahora + timedelta(days=dias)
    ).order_by("inicio")


# --------------------------------------------------------------------------- vistas de calendario (SCRUM-28)
@dataclass
class Dia:
    fecha: date
    del_mes: bool = True
    eventos: list = field(default_factory=list)

    @property
    def es_hoy(self) -> bool:
        return self.fecha == timezone.localdate()

    @property
    def laborable(self) -> bool:
        return jornada.es_laborable(self.fecha)


def _agrupar(eventos, dias: list[Dia]) -> list[Dia]:
    por_fecha = {d.fecha: d for d in dias}
    for evento in eventos:
        dia = por_fecha.get(timezone.localtime(evento.inicio).date())
        if dia:
            dia.eventos.append(evento)
    for d in dias:
        d.eventos.sort(key=lambda e: (not e.todo_el_dia, e.inicio))
    return dias


def _en_rango(usuario, desde: date, hasta: date):
    zona = timezone.get_current_timezone()
    ini = timezone.make_aware(datetime.combine(desde, time.min), zona)
    fin = timezone.make_aware(datetime.combine(hasta + timedelta(days=1), time.min), zona)
    return eventos_de(usuario).filter(inicio__gte=ini, inicio__lt=fin).exclude(estado=Evento.Estado.CANCELADO)


def semanas_del_mes(usuario, referencia: date) -> list[list[Dia]]:
    semanas = calendar.Calendar(firstweekday=0).monthdatescalendar(referencia.year, referencia.month)
    dias = [Dia(f, f.month == referencia.month) for semana in semanas for f in semana]
    _agrupar(_en_rango(usuario, dias[0].fecha, dias[-1].fecha), dias)
    return [dias[i:i + 7] for i in range(0, len(dias), 7)]


def dias_de_la_semana(usuario, referencia: date) -> list[Dia]:
    lunes = referencia - timedelta(days=referencia.weekday())
    dias = [Dia(lunes + timedelta(days=i)) for i in range(7)]
    return _agrupar(_en_rango(usuario, dias[0].fecha, dias[-1].fecha), dias)


def eventos_del_dia(usuario, dia: date) -> Dia:
    return _agrupar(eventos_de(usuario).filter(inicio__date=dia).order_by("inicio"), [Dia(dia)])[0]


# --------------------------------------------------------------------------- escritura
@transaction.atomic
def guardar_evento(evento: Evento, participantes, *, avisar_de_nuevo: bool) -> Evento:
    if avisar_de_nuevo:
        preparar_aviso(evento)
    evento.save()
    ids = {u.pk for u in participantes} - {evento.propietario_id}
    evento.participantes.exclude(usuario_id__in=ids).delete()
    existentes = set(evento.participantes.values_list("usuario_id", flat=True))
    ParticipanteEvento.objects.bulk_create([ParticipanteEvento(evento=evento, usuario_id=i) for i in ids - existentes])
    return evento


@transaction.atomic
def cambiar_estado(evento: Evento, estado: str) -> Evento:
    evento.estado = estado
    if estado == Evento.Estado.PENDIENTE:
        preparar_aviso(evento)
    evento.save(update_fields=["estado", "aviso_enviado", "actualizado_en"])
    return evento
