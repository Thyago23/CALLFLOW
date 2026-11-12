"""
Breaks de los vendedores.

El coordinador (permiso directo `asistencia.gestionar_breaks`) da un break de N minutos: le llega
una notificación a cada vendedor con la jornada abierta (no a quien está en almuerzo, ausente o ya
finalizó su día) y el tiempo corre desde ese momento. Cada vendedor marca «Volví del break»; si lo
hace después del fin (más la tolerancia), se guarda con cuántos minutos llegó tarde y se avisa al
coordinador.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.acceso import tiene_permiso_directo
from apps.personal.models import Empleado

from .models import Descanso, DescansoProgramado, RegistroAsistencia, RegresoDescanso

PERMISO = "asistencia.gestionar_breaks"
DURACION_MAXIMA = 120


def tolerancia() -> int:
    return int(getattr(settings, "ASISTENCIA_BREAK_TOLERANCIA_MINUTOS", 0))


def participantes(ahora: datetime | None = None):
    """Vendedores activos con la jornada abierta hoy y que no están en su almuerzo."""
    from apps.core.models import Rol

    hoy = timezone.localdate(ahora or timezone.now())
    abiertos = (RegistroAsistencia.objects.filter(fecha=hoy, salida__isnull=True)
                .filter(Q(almuerzo_inicio__isnull=True) | Q(almuerzo_fin__isnull=False))
                .values("empleado_id"))
    return (Empleado.objects.filter(pk__in=abiertos, estado=Empleado.Estado.ACTIVO,
                                    usuario__rol=Rol.VENDEDOR, usuario__is_active=True)
            .select_related("usuario").order_by("apellidos", "nombres"))


def en_curso(ahora: datetime | None = None) -> Descanso | None:
    """El break que todavía no termina (solo puede haber uno a la vez)."""
    return Descanso.objects.filter(fin__gt=ahora or timezone.now()).order_by("-inicio").first()


def minutos_tarde(fin: datetime, regreso: datetime) -> int:
    """Minutos de más (cada minuto empezado cuenta); 0 si volvió dentro del tiempo más la tolerancia."""
    segundos = (regreso - fin).total_seconds()
    if segundos <= 0:
        return 0
    minutos = math.ceil(segundos / 60)
    return minutos if minutos > tolerancia() else 0


def _usuarios_coordinadores(descanso: Descanso):
    from apps.core.models import Usuario

    destinatarios = {u.pk: u for u in Usuario.objects.filter(is_active=True, user_permissions__codename="gestionar_breaks",
                                                              user_permissions__content_type__app_label="asistencia")}
    if descanso.enviado_por and descanso.enviado_por.is_active:
        destinatarios.setdefault(descanso.enviado_por.pk, descanso.enviado_por)
    return destinatarios.values()


@transaction.atomic
def dar_break(usuario, minutos: int, mensaje: str = "", ahora: datetime | None = None) -> Descanso:
    if not tiene_permiso_directo(usuario, PERMISO):
        raise ValidationError("Solo el coordinador puede dar breaks.")
    if not 1 <= int(minutos) <= DURACION_MAXIMA:
        raise ValidationError(f"El break debe durar entre 1 y {DURACION_MAXIMA} minutos.")
    ahora = ahora or timezone.now()
    actual = Descanso.objects.select_for_update().filter(fin__gt=ahora).first()
    if actual:
        raise ValidationError(f"Ya hay un break en curso hasta las {timezone.localtime(actual.fin):%H:%M}.")
    vendedores = list(participantes(ahora))
    if not vendedores:
        raise ValidationError("No hay vendedores con la jornada abierta en este momento.")
    return _iniciar(usuario, vendedores, int(minutos), mensaje, ahora)


def _iniciar(usuario, vendedores, minutos: int, mensaje: str, inicio: datetime) -> Descanso:
    """Crea el break desde `inicio`, anota a cada vendedor y les avisa (lo usan el break inmediato y el programado)."""
    from apps.core.models import Notificacion

    descanso = Descanso.objects.create(duracion_minutos=minutos, mensaje=(mensaje or "").strip()[:150],
                                       inicio=inicio, fin=inicio + timedelta(minutes=minutos), enviado_por=usuario)
    RegresoDescanso.objects.bulk_create([RegresoDescanso(descanso=descanso, empleado=e) for e in vendedores])
    vuelta = timezone.localtime(descanso.fin)
    texto = f"Tienes {minutos} minutos: vuelve a las {vuelta:%H:%M} y marca «Volví del break»."
    if descanso.mensaje:
        texto = f"{descanso.mensaje} — {texto}"
    Notificacion.objects.bulk_create([
        Notificacion(destinatario=e.usuario, tipo=Notificacion.Tipo.ASISTENCIA, titulo=f"¡Break de {minutos} minutos!",
                     mensaje=texto, url=reverse("inicio"))
        for e in vendedores
    ])
    return descanso


def pendiente_de(empleado, ahora: datetime | None = None) -> RegresoDescanso | None:
    """El break de hoy del que este vendedor todavía no marcó su regreso."""
    if empleado is None:
        return None
    hoy = timezone.localdate(ahora or timezone.now())
    return (RegresoDescanso.objects.select_related("descanso")
            .filter(empleado=empleado, regreso_en__isnull=True, descanso__inicio__date=hoy)
            .order_by("-descanso__inicio").first())


@transaction.atomic
def volver_de_break(empleado, ahora: datetime | None = None) -> RegresoDescanso:
    from apps.core.models import Notificacion

    ahora = ahora or timezone.now()
    regreso = pendiente_de(empleado, ahora)
    if regreso is None:
        raise ValidationError("No tiene un break pendiente de marcar.")
    regreso = RegresoDescanso.objects.select_for_update(of=("self",)).select_related("descanso__enviado_por").get(pk=regreso.pk)
    if regreso.regreso_en:
        return regreso  # doble clic: ya quedó registrado
    regreso.regreso_en = ahora
    regreso.minutos_tarde = minutos_tarde(regreso.descanso.fin, ahora)
    regreso.save(update_fields=["regreso_en", "minutos_tarde"])
    if regreso.minutos_tarde:
        fin, hora = timezone.localtime(regreso.descanso.fin), timezone.localtime(ahora)
        Notificacion.objects.bulk_create([
            Notificacion(destinatario=u, tipo=Notificacion.Tipo.ASISTENCIA,
                         titulo=f"{empleado} volvió {regreso.minutos_tarde} min tarde del break"[:150],
                         mensaje=f"El break terminaba a las {fin:%H:%M} y marcó su regreso a las {hora:%H:%M}.",
                         url=reverse("asistencia:breaks"))
            for u in _usuarios_coordinadores(regreso.descanso)
        ])
    return regreso


@dataclass
class FilaBreak:
    regreso: RegresoDescanso
    estado: str  # a_tiempo | tarde | en_break | sin_volver
    minutos: int = 0  # de retraso (tarde o sin volver todavía)


def detalle(descanso: Descanso, ahora: datetime | None = None) -> dict:
    """Cómo va cada vendedor en un break, para la pantalla del coordinador."""
    ahora = ahora or timezone.now()
    filas = []
    for r in descanso.regresos.select_related("empleado").order_by("empleado__apellidos", "empleado__nombres"):
        if r.regreso_en:
            filas.append(FilaBreak(r, "tarde" if r.minutos_tarde else "a_tiempo", r.minutos_tarde or 0))
        elif ahora <= descanso.fin:
            filas.append(FilaBreak(r, "en_break"))
        else:
            filas.append(FilaBreak(r, "sin_volver", math.ceil((ahora - descanso.fin).total_seconds() / 60)))
    # Primero lo que hay que mirar: quien no vuelve y quien llegó tarde.
    orden = {"sin_volver": 0, "tarde": 1, "en_break": 2, "a_tiempo": 3}
    filas.sort(key=lambda f: (orden[f.estado], -f.minutos))
    cuenta = {e: sum(1 for f in filas if f.estado == e) for e in orden}
    return {"descanso": descanso, "filas": filas, "cuenta": cuenta, "terminado": ahora > descanso.fin}


# --------------------------------------------------------------------------- breaks programados
# El coordinador programa un break para una hora: `aviso_minutos` antes (10 o 5) se les recuerda a los
# vendedores con la jornada abierta y a la hora empieza solo, como un break normal. No hay un proceso
# aparte: lo hace `procesar_programados`, que corre con el sondeo de la campana (cada 5 s en cada
# navegador abierto) y con el comando `procesar_breaks` (para un cron, si se quiere).
PROGRAMAR_HASTA_DIAS = 30


def _hora(momento: datetime) -> str:
    return f"{timezone.localtime(momento):%H:%M}"


def _se_cruzan(inicio: datetime, minutos: int, excluir=None) -> DescansoProgramado | None:
    """Otro break programado pendiente cuyo horario se cruza con [inicio, inicio + minutos)."""
    fin = inicio + timedelta(minutes=minutos)
    for p in DescansoProgramado.objects.filter(estado__in=DescansoProgramado.ACTIVOS, programado_para__lt=fin,
                                               programado_para__gt=inicio - timedelta(minutes=DURACION_MAXIMA)).exclude(pk=excluir):
        if p.fin > inicio:
            return p
    return None


@transaction.atomic
def programar_break(usuario, inicio: datetime, minutos: int, aviso: int = 10, mensaje: str = "",
                    ahora: datetime | None = None) -> DescansoProgramado:
    if not tiene_permiso_directo(usuario, PERMISO):
        raise ValidationError("Solo el coordinador puede programar breaks.")
    ahora = ahora or timezone.now()
    minutos, aviso = int(minutos), int(aviso)
    if not 1 <= minutos <= DURACION_MAXIMA:
        raise ValidationError(f"El break debe durar entre 1 y {DURACION_MAXIMA} minutos.")
    if aviso not in DescansoProgramado.AVISOS:
        raise ValidationError("El recordatorio puede ser 10 o 5 minutos antes.")
    inicio = inicio.replace(second=0, microsecond=0)
    if inicio <= ahora:
        raise ValidationError("La hora del break ya pasó: elija una hora futura (o use «Dar un break» para darlo ahora).")
    if inicio > ahora + timedelta(days=PROGRAMAR_HASTA_DIAS):
        raise ValidationError(f"Se puede programar hasta con {PROGRAMAR_HASTA_DIAS} días de anticipación.")
    # Bloquea los pendientes para que dos coordinadores no programen a la vez en el mismo horario.
    list(DescansoProgramado.objects.select_for_update().filter(estado__in=DescansoProgramado.ACTIVOS))
    otro = _se_cruzan(inicio, minutos)
    if otro:
        raise ValidationError(f"Se cruza con el break programado de las {_hora(otro.programado_para)} a las {_hora(otro.fin)}.")
    return DescansoProgramado.objects.create(programado_para=inicio, duracion_minutos=minutos, aviso_minutos=aviso,
                                             mensaje=(mensaje or "").strip()[:150], creado_por=usuario)


@transaction.atomic
def cancelar_programado(usuario, pk: int, ahora: datetime | None = None) -> DescansoProgramado:
    from apps.core.models import Notificacion

    if not tiene_permiso_directo(usuario, PERMISO):
        raise ValidationError("Solo el coordinador puede cancelar breaks programados.")
    p = DescansoProgramado.objects.select_for_update().filter(pk=pk).first()
    if p is None or not p.activo:
        raise ValidationError("Ese break ya no está pendiente (empezó, se omitió o ya se canceló).")
    avisado = p.estado == DescansoProgramado.Estado.AVISADO
    p.estado, p.cancelado_por = DescansoProgramado.Estado.CANCELADO, usuario
    p.motivo = f"Cancelado por {usuario.get_full_name() or usuario.username}"[:200]
    p.save(update_fields=["estado", "cancelado_por", "motivo", "actualizado_en"])
    if avisado:  # ya les había llegado el recordatorio: que sepan que no hay break
        Notificacion.objects.bulk_create([
            Notificacion(destinatario=e.usuario, tipo=Notificacion.Tipo.ASISTENCIA,
                         titulo=f"Se canceló el break de las {_hora(p.programado_para)}",
                         mensaje="Sigue con tu jornada normal.", url=reverse("inicio"))
            for e in participantes(ahora)
        ])
    return p


def _recordar(p: DescansoProgramado, ahora: datetime) -> None:
    from apps.core.models import Notificacion

    vendedores = list(participantes(ahora))
    faltan = max(1, math.ceil((p.programado_para - ahora).total_seconds() / 60))
    texto = f"A las {_hora(p.programado_para)} empieza tu break de {p.duracion_minutos} min. Ve cerrando lo que estás haciendo."
    if p.mensaje:
        texto = f"{p.mensaje} — {texto}"
    Notificacion.objects.bulk_create([
        Notificacion(destinatario=e.usuario, tipo=Notificacion.Tipo.ASISTENCIA,
                     titulo=f"Tu break empieza en {faltan} minuto{'s' if faltan != 1 else ''}", mensaje=texto, url=reverse("inicio"))
        for e in vendedores
    ])
    p.estado, p.avisado_en, p.avisados = DescansoProgramado.Estado.AVISADO, ahora, len(vendedores)
    p.save(update_fields=["estado", "avisado_en", "avisados", "actualizado_en"])


def _omitir(p: DescansoProgramado, motivo: str) -> None:
    from apps.core.models import Notificacion

    p.estado, p.motivo = DescansoProgramado.Estado.OMITIDO, motivo[:200]
    p.save(update_fields=["estado", "motivo", "actualizado_en"])
    if p.creado_por and p.creado_por.is_active:
        Notificacion.objects.create(destinatario=p.creado_por, tipo=Notificacion.Tipo.ASISTENCIA,
                                    titulo=f"El break programado de las {_hora(p.programado_para)} no empezó",
                                    mensaje=motivo[:300], url=reverse("asistencia:breaks"))


def _empezar(p: DescansoProgramado, ahora: datetime) -> None:
    if ahora >= p.fin:
        return _omitir(p, "Nadie tenía CALLFLOW abierto a esa hora y el horario del break ya pasó.")
    if Descanso.objects.select_for_update().filter(fin__gt=p.programado_para, inicio__lte=ahora).exists():
        return _omitir(p, "A esa hora ya había otro break en curso.")
    vendedores = list(participantes(ahora))
    if not vendedores:
        return _omitir(p, "A esa hora no había vendedores con la jornada abierta.")
    # El tiempo corre desde la hora programada, aunque se procese unos segundos después.
    p.descanso = _iniciar(p.creado_por, vendedores, p.duracion_minutos, p.mensaje, p.programado_para)
    p.estado = DescansoProgramado.Estado.INICIADO
    p.save(update_fields=["descanso", "estado", "actualizado_en"])


def procesar_programados(ahora: datetime | None = None) -> int:
    """Envía los recordatorios que tocan y empieza los breaks que llegaron a su hora. Devuelve cuántos cambió."""
    ahora = ahora or timezone.now()
    tope = ahora + timedelta(minutes=max(DescansoProgramado.AVISOS))
    activos = DescansoProgramado.objects.filter(estado__in=DescansoProgramado.ACTIVOS, programado_para__lte=tope)
    if not activos.exists():  # lo normal: una consulta y nada más
        return 0
    cambios = 0
    with transaction.atomic():
        # skip_locked: si dos navegadores sondean a la vez, solo uno procesa cada break.
        for p in activos.select_for_update(skip_locked=True, of=("self",)).select_related("creado_por").order_by("programado_para"):
            if ahora >= p.programado_para:
                _empezar(p, ahora)
                cambios += 1
            elif p.estado == DescansoProgramado.Estado.PROGRAMADO and ahora >= p.avisar_en:
                _recordar(p, ahora)
                cambios += 1
    return cambios


def proximo_para(empleado, ahora: datetime | None = None) -> DescansoProgramado | None:
    """El break programado que está por empezar (ya con recordatorio) para un vendedor con la jornada abierta."""
    if empleado is None:
        return None
    ahora = ahora or timezone.now()
    p = (DescansoProgramado.objects.filter(estado=DescansoProgramado.Estado.AVISADO, programado_para__gt=ahora)
         .order_by("programado_para").first())
    if p is None or not participantes(ahora).filter(pk=empleado.pk).exists():
        return None
    return p


def programados(ahora: datetime | None = None) -> dict:
    """Para la pantalla del coordinador: los pendientes y lo que pasó con los de hoy."""
    ahora = ahora or timezone.now()
    hoy = timezone.localdate(ahora)
    return {
        "pendientes": list(DescansoProgramado.objects.filter(estado__in=DescansoProgramado.ACTIVOS)
                           .select_related("creado_por").order_by("programado_para")),
        "resueltos_hoy": list(DescansoProgramado.objects.exclude(estado__in=DescansoProgramado.ACTIVOS)
                              .filter(programado_para__date=hoy).select_related("descanso").order_by("-programado_para")),
    }
