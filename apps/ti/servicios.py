"""Reglas del módulo de TI: conexiones a CALLFLOW, inventario de equipos y su hoja de vida."""
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import Equipo, EventoEquipo, SesionEquipo

# Cuántos minutos sin actividad para dejar de considerar a alguien «conectado».
MINUTOS_CONECTADO = getattr(settings, "TI_MINUTOS_CONECTADO", 5)
# Cada cuántos segundos se actualiza la última actividad de una sesión (evita escribir en cada petición).
SEGUNDOS_ACTIVIDAD = 60
CLAVE_ACTIVIDAD = "_ti_actividad"


# --------------------------------------------------------------------------- equipo desde el que se conecta
def ip_de(request) -> str | None:
    """IP del cliente. Detrás de un proxy (Nginx, IIS) se toma X-Forwarded-For solo si TI_PROXY_CONFIABLE está activo."""
    if getattr(settings, "TI_PROXY_CONFIABLE", False):
        reenviada = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if reenviada:
            return reenviada.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


_NAVEGADORES = [(r"Edg(?:e|A|iOS)?/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/|FxiOS/", "Firefox"),
                (r"Chrome/|CriOS/", "Chrome"), (r"Safari/", "Safari")]
_MARCA_VERSION = {"Edge": r"Edg(?:e|A|iOS)?", "Opera": "OPR", "Firefox": "(?:Firefox|FxiOS)", "Chrome": "(?:Chrome|CriOS)",
                  "Safari": "Version"}
_SISTEMAS = [(r"Windows NT 10", "Windows 10/11"), (r"Windows NT 6\.3", "Windows 8.1"), (r"Windows NT 6\.1", "Windows 7"),
             (r"Windows", "Windows"), (r"Android", "Android"), (r"iPhone|iPad|iOS", "iOS"), (r"Mac OS X|Macintosh", "macOS"),
             (r"CrOS", "ChromeOS"), (r"Linux", "Linux")]


def describir_agente(agente: str) -> dict:
    """Navegador, sistema operativo y tipo de dispositivo a partir del User-Agent (sin librerías externas)."""
    agente = agente or ""
    navegador = next((n for patron, n in _NAVEGADORES if re.search(patron, agente)), "Otro")
    marca_version = _MARCA_VERSION.get(navegador)
    version = re.search(rf"{marca_version}/(\d+)", agente) if marca_version else None
    sistema = next((n for patron, n in _SISTEMAS if re.search(patron, agente)), "Desconocido")
    if re.search(r"iPad|Tablet", agente):
        dispositivo = "Tablet"
    elif re.search(r"Mobi|Android|iPhone", agente):
        dispositivo = "Celular"
    else:
        dispositivo = "Computador"
    return {"navegador": f"{navegador} {version.group(1)}" if version else navegador, "sistema": sistema, "dispositivo": dispositivo}


def _clave_sesion(request) -> str | None:
    sesion = getattr(request, "session", None)
    if sesion is None:
        return None
    if not sesion.session_key:
        sesion.save()
    return sesion.session_key


def registrar_inicio(request, usuario) -> SesionEquipo | None:
    clave = _clave_sesion(request)
    if not clave:
        return None
    ahora = timezone.now()
    agente = request.META.get("HTTP_USER_AGENT", "")[:1000]
    sesion, _ = SesionEquipo.objects.update_or_create(
        clave_sesion=clave,
        defaults={"usuario": usuario, "ip": ip_de(request), "agente": agente, **describir_agente(agente),
                  "inicio": ahora, "ultima_actividad": ahora, "fin": None, "motivo_fin": ""},
    )
    request.session[CLAVE_ACTIVIDAD] = ahora.timestamp()
    return sesion


def registrar_actividad(request) -> None:
    """La llama el middleware: como mucho una escritura por minuto y sesión."""
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return
    ahora = timezone.now()
    ultima = request.session.get(CLAVE_ACTIVIDAD)
    if ultima and ahora.timestamp() - ultima < SEGUNDOS_ACTIVIDAD:
        return
    clave = _clave_sesion(request)
    actualizadas = SesionEquipo.objects.filter(clave_sesion=clave, fin__isnull=True).update(ultima_actividad=ahora, ip=ip_de(request))
    if not actualizadas and not SesionEquipo.objects.filter(clave_sesion=clave).exists():
        registrar_inicio(request, usuario)  # sesiones abiertas antes de existir el módulo de TI
        return
    request.session[CLAVE_ACTIVIDAD] = ahora.timestamp()


def registrar_cierre(request) -> None:
    sesion = getattr(request, "session", None)
    if sesion is not None and sesion.session_key:
        SesionEquipo.objects.filter(clave_sesion=sesion.session_key, fin__isnull=True).update(
            fin=timezone.now(), motivo_fin=SesionEquipo.Fin.CIERRE)


def conectados():
    """Sesiones abiertas con actividad en los últimos MINUTOS_CONECTADO minutos."""
    limite = timezone.now() - timedelta(minutes=MINUTOS_CONECTADO)
    return (SesionEquipo.objects.filter(fin__isnull=True, ultima_actividad__gte=limite)
            .select_related("usuario").order_by("usuario__first_name", "usuario__last_name"))


def equipos_por_ip(ips) -> dict:
    """{ip: Equipo} de los equipos registrados con esas IP."""
    ips = {i for i in ips if i}
    return {e.direccion_ip: e for e in Equipo.objects.filter(direccion_ip__in=ips).select_related("asignado_a")} if ips else {}


@transaction.atomic
def cerrar_sesion_remota(sesion: SesionEquipo, por) -> None:
    """Cierra la sesión en el servidor: el usuario tendrá que volver a ingresar en ese equipo."""
    from django.contrib.sessions.models import Session

    Session.objects.filter(session_key=sesion.clave_sesion).delete()
    sesion.fin = timezone.now()
    sesion.motivo_fin = SesionEquipo.Fin.CERRADA_TI
    sesion.cerrada_por = por
    sesion.save(update_fields=["fin", "motivo_fin", "cerrada_por"])


# --------------------------------------------------------------------------- inventario de equipos
def siguiente_codigo() -> str:
    numeros = [int(m.group(1)) for c in Equipo.objects.filter(codigo__regex=r"^EQ-[0-9]+$").values_list("codigo", flat=True)
               if (m := re.match(r"^EQ-(\d+)$", c))]
    return f"EQ-{max(numeros, default=0) + 1:04d}"


def _empleado_texto(empleado) -> str:
    return f"{empleado.nombres} {empleado.apellidos}" if empleado else ""


@transaction.atomic
def guardar_equipo(equipo: Equipo, *, usuario, anterior: dict | None = None) -> Equipo:
    """
    Guarda el equipo y anota en su hoja de vida lo que cambió: alta, asignación, devolución y estado.
    `anterior` = {"asignado_a": Empleado|None, "estado": str} antes de editar (None si es nuevo).
    """
    nuevo = equipo.pk is None
    if not equipo.codigo:
        equipo.codigo = siguiente_codigo()
    if nuevo:
        equipo.registrado_por = usuario
    equipo.save()
    hoy = timezone.localdate()

    def evento(tipo, descripcion, empleado=None):
        EventoEquipo.objects.create(equipo=equipo, fecha=hoy, tipo=tipo, descripcion=descripcion, empleado=empleado,
                                    registrado_por=usuario)

    if nuevo:
        evento(EventoEquipo.Tipo.ALTA, f"Alta en el inventario como {equipo.get_tipo_display().lower()}"
               + (f" ({equipo.get_estado_display().lower()})." if equipo.estado != Equipo.Estado.OPERATIVO else "."))
        if equipo.asignado_a:
            evento(EventoEquipo.Tipo.ASIGNACION, f"Asignado a {_empleado_texto(equipo.asignado_a)}.", equipo.asignado_a)
        return equipo

    antes = anterior.get("asignado_a")
    if antes != equipo.asignado_a:
        if antes:
            evento(EventoEquipo.Tipo.DEVOLUCION, f"Devuelto por {_empleado_texto(antes)}.", antes)
        if equipo.asignado_a:
            evento(EventoEquipo.Tipo.ASIGNACION, f"Asignado a {_empleado_texto(equipo.asignado_a)}.", equipo.asignado_a)
    if anterior.get("estado") != equipo.estado:
        tipo = EventoEquipo.Tipo.BAJA if equipo.estado == Equipo.Estado.BAJA else EventoEquipo.Tipo.CAMBIO_ESTADO
        de = dict(Equipo.Estado.choices).get(anterior.get("estado"), anterior.get("estado"))
        evento(tipo, f"Estado: {de} → {equipo.get_estado_display()}.")
    return equipo


def resumen() -> dict:
    hoy = timezone.localdate()
    en_uso = Equipo.objects.exclude(estado=Equipo.Estado.BAJA)
    return {
        "total": en_uso.count(),
        "operativos": en_uso.filter(estado=Equipo.Estado.OPERATIVO).count(),
        "reparacion": en_uso.filter(estado=Equipo.Estado.REPARACION).count(),
        "bodega": en_uso.filter(estado=Equipo.Estado.BODEGA).count(),
        "garantia_por_vencer": en_uso.filter(garantia_hasta__gte=hoy, garantia_hasta__lte=hoy + timedelta(days=30)).count(),
        "conectados": conectados().values("usuario").distinct().count(),
    }


def ultima_conexion_por_ip(ips) -> dict:
    """{ip: fecha de la última actividad} para mostrar cuándo se usó cada equipo por última vez."""
    ips = [i for i in ips if i]
    if not ips:
        return {}
    return dict(SesionEquipo.objects.filter(ip__in=ips).values("ip").annotate(u=Max("ultima_actividad")).values_list("ip", "u"))


def conexiones_por_usuario(desde, hasta):
    """Para el reporte: sesiones, IP distintas y última conexión de cada usuario en el rango."""
    return (SesionEquipo.objects.filter(inicio__date__gte=desde, inicio__date__lte=hasta)
            .values("usuario__username", "usuario__first_name", "usuario__last_name", "usuario__rol")
            .annotate(sesiones=Count("pk"), ips=Count("ip", distinct=True), ultima=Max("ultima_actividad"),
                      cerradas_ti=Count("pk", filter=Q(motivo_fin=SesionEquipo.Fin.CERRADA_TI)))
            .order_by("usuario__first_name", "usuario__last_name"))
