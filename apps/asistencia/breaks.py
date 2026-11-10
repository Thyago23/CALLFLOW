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

from .models import Descanso, RegistroAsistencia, RegresoDescanso

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
    from apps.core.models import Notificacion

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

    descanso = Descanso.objects.create(duracion_minutos=int(minutos), mensaje=(mensaje or "").strip()[:150],
                                       inicio=ahora, fin=ahora + timedelta(minutes=int(minutos)), enviado_por=usuario)
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
