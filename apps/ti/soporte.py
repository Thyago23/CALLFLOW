"""
Mesa de ayuda: cualquier persona reporta una falla y TI la atiende con estado, prioridad y tiempo de respuesta.

Plazos (settings.TI_MESA_PLAZOS, en minutos, por prioridad): (primera respuesta, solución). Se cuentan en tiempo
corrido desde que se creó el reporte. Cuenta como primera respuesta lo primero que TI hace y la persona ve:
una respuesta escrita o un cambio de estado. Las notas internas de TI no cuentan.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol, Usuario

from .models import ComentarioTicket, Equipo, Ticket
from .servicios import OperacionNoPermitida, ip_de

# (minutos para la primera respuesta, minutos para la solución)
PLAZOS_POR_DEFECTO = {
    Ticket.Prioridad.CRITICA: (15, 4 * 60),
    Ticket.Prioridad.ALTA: (30, 8 * 60),
    Ticket.Prioridad.MEDIA: (2 * 60, 24 * 60),
    Ticket.Prioridad.BAJA: (8 * 60, 72 * 60),
}


def plazos(prioridad: str) -> tuple[timedelta, timedelta]:
    respuesta, solucion = (PLAZOS_POR_DEFECTO | getattr(settings, "TI_MESA_PLAZOS", {}))[prioridad]
    return timedelta(minutes=respuesta), timedelta(minutes=solucion)


def tabla_plazos() -> list[dict]:
    """Para mostrar a quien reporta y a TI cuánto se tarda cada prioridad."""
    return [{"prioridad": etiqueta, "respuesta": plazos(valor)[0], "solucion": plazos(valor)[1]}
            for valor, etiqueta in Ticket.Prioridad.choices]


def es_ti(usuario) -> bool:
    return bool(usuario and usuario.is_authenticated and usuario.rol == Rol.TI)


def usuarios_ti():
    return Usuario.objects.filter(rol=Rol.TI, is_active=True)


def puede_ver(usuario, ticket: Ticket) -> bool:
    return es_ti(usuario) or ticket.reportado_por_id == usuario.pk


def _notificar(destinatarios, ticket: Ticket, titulo: str, mensaje: str = "") -> None:
    url = reverse("soporte:detalle", args=[ticket.pk])
    Notificacion.objects.bulk_create([
        Notificacion(destinatario=u, tipo=Notificacion.Tipo.SOPORTE, titulo=titulo[:150], mensaje=mensaje[:500], url=url)
        for u in destinatarios
    ])


def _nombre(usuario) -> str:
    return usuario.get_full_name() or usuario.get_username()


# --------------------------------------------------------------------------- crear y conversar
@transaction.atomic
def crear_ticket(*, usuario, titulo: str, categoria: str, prioridad: str, descripcion: str = "", request=None) -> Ticket:
    if prioridad == Ticket.Prioridad.CRITICA and not es_ti(usuario):
        prioridad = Ticket.Prioridad.ALTA  # la prioridad crítica la pone TI (afecta a varias personas)
    ahora = timezone.now()
    ip = ip_de(request) if request is not None else None
    respuesta, solucion = plazos(prioridad)
    ticket = Ticket.objects.create(
        reportado_por=usuario, titulo=titulo.strip(), descripcion=descripcion.strip(), categoria=categoria, prioridad=prioridad,
        ip=ip, equipo=Equipo.objects.filter(direccion_ip=ip).first() if ip else None,
        limite_respuesta=ahora + respuesta, limite_solucion=ahora + solucion,
    )
    _notificar(usuarios_ti().exclude(pk=usuario.pk), ticket, f"Nuevo reporte {ticket.numero}: {ticket.titulo}",
               f"{_nombre(usuario)} · {ticket.get_categoria_display()} · prioridad {ticket.get_prioridad_display().split(' (')[0].lower()}")
    return ticket


def _marcar_primera_respuesta(ticket: Ticket, usuario, ahora) -> None:
    if ticket.primera_respuesta_en is None:
        ticket.primera_respuesta_en = max(ahora, ticket.creado_en)
    if ticket.asignado_a_id is None:
        ticket.asignado_a = usuario


@transaction.atomic
def comentar(ticket: Ticket, autor, texto: str, *, interno: bool = False) -> ComentarioTicket:
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    texto = texto.strip()
    if not texto:
        raise OperacionNoPermitida("Escriba el mensaje.")
    de_ti = es_ti(autor)
    if not de_ti:
        interno = False
        if ticket.reportado_por_id != autor.pk:
            raise OperacionNoPermitida("Solo quien reportó la falla puede escribir en este reporte.")
        if ticket.estado == Ticket.Estado.CERRADO:
            raise OperacionNoPermitida("Este reporte ya está cerrado. Si la falla volvió, cree un reporte nuevo.")
    cambio = ""
    if de_ti and not interno:
        _marcar_primera_respuesta(ticket, autor, timezone.now())
        if ticket.estado == Ticket.Estado.ABIERTO:
            ticket.estado = Ticket.Estado.EN_PROCESO
            cambio = f"Estado: {Ticket.Estado.ABIERTO.label} → {Ticket.Estado.EN_PROCESO.label}"
        ticket.save()
    else:
        ticket.save(update_fields=["actualizado_en"])
    comentario = ComentarioTicket.objects.create(ticket=ticket, autor=autor, texto=texto, interno=interno, cambio=cambio)
    if de_ti and not interno and ticket.reportado_por_id != autor.pk:
        _notificar([ticket.reportado_por], ticket, f"TI respondió su reporte {ticket.numero}", texto[:200])
    elif not de_ti:
        destino = [ticket.asignado_a] if ticket.asignado_a_id else usuarios_ti()
        _notificar(destino, ticket, f"{_nombre(autor)} escribió en {ticket.numero}", texto[:200])
    return comentario


@transaction.atomic
def gestionar(ticket: Ticket, usuario, *, estado: str, prioridad: str, asignado_a=None, equipo=None,
              solucion: str = "", nota: str = "") -> Ticket:
    """Cambios que hace TI. Deja cada cambio anotado en la conversación (lo ve quien reportó)."""
    if not es_ti(usuario):
        raise OperacionNoPermitida("Solo TI gestiona los reportes.")
    ticket = Ticket.objects.select_for_update(of=("self",)).select_related("asignado_a", "equipo").get(pk=ticket.pk)
    solucion = (solucion or "").strip()
    nota = (nota or "").strip()
    if estado in (Ticket.Estado.RESUELTO, Ticket.Estado.CERRADO) and not solucion:
        raise OperacionNoPermitida("Para marcarlo como resuelto escriba qué se hizo (la solución).")
    ahora = timezone.now()
    cambios = []
    if estado != ticket.estado:
        cambios.append(f"Estado: {ticket.get_estado_display()} → {Ticket.Estado(estado).label}")
    if prioridad != ticket.prioridad:
        cambios.append(f"Prioridad: {ticket.get_prioridad_display().split(' (')[0]} → {Ticket.Prioridad(prioridad).label.split(' (')[0]}")
        respuesta, plazo_solucion = plazos(prioridad)
        ticket.limite_respuesta, ticket.limite_solucion = ticket.creado_en + respuesta, ticket.creado_en + plazo_solucion
    if (asignado_a.pk if asignado_a else None) != ticket.asignado_a_id:
        cambios.append(f"Atiende: {_nombre(asignado_a) if asignado_a else 'nadie'}")
    if (equipo.pk if equipo else None) != ticket.equipo_id:
        cambios.append(f"Equipo: {equipo.codigo if equipo else 'ninguno'}")
    if solucion != ticket.solucion and solucion:
        cambios.append("Solución actualizada")
    if not cambios and not nota:
        return ticket

    estado_anterior = ticket.estado
    ticket.estado, ticket.prioridad, ticket.asignado_a, ticket.equipo, ticket.solucion = estado, prioridad, asignado_a, equipo, solucion
    if estado != Ticket.Estado.ABIERTO or nota:
        _marcar_primera_respuesta(ticket, usuario, ahora)  # si nadie lo atiende, queda a cargo de quien lo movió
    if estado in (Ticket.Estado.RESUELTO, Ticket.Estado.CERRADO):
        ticket.resuelto_en = ticket.resuelto_en or ahora
        ticket.cerrado_en = ahora if estado == Ticket.Estado.CERRADO else None
    else:  # reabierto por TI
        ticket.resuelto_en = ticket.cerrado_en = None
    ticket.save()
    ComentarioTicket.objects.create(ticket=ticket, autor=usuario, texto=nota, cambio="; ".join(cambios)[:255])

    if ticket.reportado_por_id != usuario.pk and (estado != estado_anterior or nota):
        if estado == Ticket.Estado.RESUELTO:
            titulo = f"Su reporte {ticket.numero} está resuelto: confirme si quedó bien"
        else:
            titulo = f"Su reporte {ticket.numero} está {ticket.get_estado_display().lower()}"
        _notificar([ticket.reportado_por], ticket, titulo, solucion[:200] if estado == Ticket.Estado.RESUELTO else nota[:200])
    return ticket


@transaction.atomic
def confirmar(ticket: Ticket, usuario, *, solucionado: bool, texto: str = "") -> Ticket:
    """Quien reportó confirma la solución (se cierra) o avisa que sigue fallando (se reabre)."""
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.reportado_por_id != usuario.pk:
        raise OperacionNoPermitida("Solo quien reportó la falla puede confirmar la solución.")
    if ticket.estado != Ticket.Estado.RESUELTO:
        raise OperacionNoPermitida("Este reporte no está esperando su confirmación.")
    texto = texto.strip()
    if solucionado:
        ticket.estado, ticket.cerrado_en = Ticket.Estado.CERRADO, timezone.now()
        cambio = f"Estado: {Ticket.Estado.RESUELTO.label} → {Ticket.Estado.CERRADO.label} (confirmado por quien reportó)"
    else:
        ticket.estado, ticket.resuelto_en, ticket.cerrado_en = Ticket.Estado.ABIERTO, None, None
        cambio = f"Estado: {Ticket.Estado.RESUELTO.label} → {Ticket.Estado.ABIERTO.label} (sigue fallando)"
    ticket.save()
    ComentarioTicket.objects.create(ticket=ticket, autor=usuario, texto=texto, cambio=cambio)
    destino = [ticket.asignado_a] if ticket.asignado_a_id else usuarios_ti()
    if solucionado:
        _notificar(destino, ticket, f"{ticket.numero} confirmado como solucionado", texto[:200])
    else:
        _notificar(destino, ticket, f"{ticket.numero} sigue fallando: se reabrió", texto[:200])
    return ticket


# --------------------------------------------------------------------------- indicadores
def sin_atender() -> int:
    """Reportes que nadie de TI ha tomado todavía (contador del menú)."""
    return Ticket.objects.filter(estado=Ticket.Estado.ABIERTO, primera_respuesta_en__isnull=True).count()


def resumen() -> dict:
    ahora = timezone.now()
    activos = Ticket.objects.filter(estado__in=Ticket.ACTIVOS)
    return activos.aggregate(
        activos=Count("pk"),
        sin_atender=Count("pk", filter=Q(primera_respuesta_en__isnull=True)),
        respuesta_vencida=Count("pk", filter=Q(primera_respuesta_en__isnull=True, limite_respuesta__lt=ahora)),
        solucion_vencida=Count("pk", filter=Q(limite_solucion__lt=ahora)),
        criticos=Count("pk", filter=Q(prioridad=Ticket.Prioridad.CRITICA)),
    ) | {"por_confirmar": Ticket.objects.filter(estado=Ticket.Estado.RESUELTO).count()}


def indicadores(dias: int = 30) -> dict:
    """Tiempos de respuesta y solución de los reportes creados en los últimos `dias` días."""
    desde = timezone.now() - timedelta(days=dias)
    qs = Ticket.objects.filter(creado_en__gte=desde)
    duracion = DurationField()
    datos = qs.aggregate(
        total=Count("pk"),
        respondidos=Count("pk", filter=Q(primera_respuesta_en__isnull=False)),
        respondidos_a_tiempo=Count("pk", filter=Q(primera_respuesta_en__lte=F("limite_respuesta"))),
        resueltos=Count("pk", filter=Q(resuelto_en__isnull=False)),
        resueltos_a_tiempo=Count("pk", filter=Q(resuelto_en__lte=F("limite_solucion"))),
        promedio_respuesta=Avg(ExpressionWrapper(F("primera_respuesta_en") - F("creado_en"), output_field=duracion)),
        promedio_solucion=Avg(ExpressionWrapper(F("resuelto_en") - F("creado_en"), output_field=duracion)),
    )
    datos["pct_respuesta"] = round(datos["respondidos_a_tiempo"] * 100 / datos["respondidos"]) if datos["respondidos"] else None
    datos["pct_solucion"] = round(datos["resueltos_a_tiempo"] * 100 / datos["resueltos"]) if datos["resueltos"] else None
    datos["por_categoria"] = [
        {"categoria": Ticket.Categoria(c["categoria"]).label, "n": c["n"]}
        for c in qs.values("categoria").annotate(n=Count("pk")).order_by("-n")
    ]
    datos["dias"] = dias
    return datos
