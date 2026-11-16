"""
Avisos automáticos a TI (campana y notificación push):

- licencias y servicios que vencen dentro de sus días de aviso, y los que ya vencieron;
- mantenimientos preventivos que tocan pronto o ya se pasaron;
- respaldos que fallaron o que están atrasados;
- disco del servidor con poco espacio;
- cuentas bloqueadas por intentos fallidos (lo llama apps.core.limite_peticiones en el momento).

Cada aviso se manda una sola vez (tabla AvisoTI, por clave). La revisión corre con el comando
`python manage.py revisar_alertas_ti` (para el Programador de tareas) y, además, sola cada
INTERVALO_REVISION segundos desde la consulta periódica de la campana.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol, Usuario

from .models import AvisoTI, Equipo, Licencia, PlanMantenimiento

logger = logging.getLogger(__name__)

INTERVALO_REVISION = 10 * 60
_CLAVE_CACHE = "ti-alertas-revision"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def avisar(clave: str, titulo: str, mensaje: str = "", url: str = "") -> bool:
    """Notifica a todas las cuentas de TI, solo la primera vez que se usa `clave`. Devuelve si avisó."""
    clave = clave[:150]
    if AvisoTI.objects.filter(clave=clave).exists():
        return False
    with transaction.atomic():  # punto de guardado: un fallo aquí no rompe la transacción de quien llama
        _, creado = AvisoTI.objects.get_or_create(clave=clave, defaults={"titulo": titulo[:150]})
        if creado:
            Notificacion.objects.bulk_create([
                Notificacion(destinatario=u, tipo=Notificacion.Tipo.AVISO_TI, titulo=titulo[:150], mensaje=mensaje[:500], url=url)
                for u in Usuario.objects.filter(rol=Rol.TI, is_active=True)
            ])
    return creado


def revisar_licencias(hoy=None) -> int:
    hoy = hoy or timezone.localdate()
    url = reverse("ti:licencias")
    enviados = 0
    for lic in Licencia.objects.filter(activa=True, fecha_vencimiento__isnull=False):
        dias = (lic.fecha_vencimiento - hoy).days
        fecha = f"{lic.fecha_vencimiento:%d/%m/%Y}"
        if dias < 0:
            enviados += avisar(f"licencia:{lic.pk}:{lic.fecha_vencimiento}:vencida", f"Venció {lic.nombre}",
                               f"{lic.get_tipo_display()} · venció el {fecha}" + (f" · {lic.proveedor}" if lic.proveedor else ""), url)
        elif dias <= lic.dias_aviso:
            cuando = "hoy" if dias == 0 else f"en {_plural(dias, 'día', 'días')}"
            enviados += avisar(f"licencia:{lic.pk}:{lic.fecha_vencimiento}:previo", f"{lic.nombre} vence {cuando}",
                               f"{lic.get_tipo_display()} · vence el {fecha}"
                               + (" · se renueva sola: revise que el cobro pase" if lic.renovacion_automatica else " · hay que renovarla"),
                               url)
    return enviados


def revisar_mantenimientos(hoy=None) -> int:
    hoy = hoy or timezone.localdate()
    enviados = 0
    planes = (PlanMantenimiento.objects.filter(activo=True).exclude(equipo__estado=Equipo.Estado.BAJA)
              .select_related("equipo"))
    for plan in planes:
        dias = (plan.proxima_fecha - hoy).days
        url = reverse("ti:mantenimiento") + f"?q={plan.equipo.codigo}"
        if dias < 0:
            enviados += avisar(f"mantenimiento:{plan.pk}:{plan.proxima_fecha}:vencido",
                               f"Mantenimiento atrasado: {plan.equipo.codigo}",
                               f"{plan.actividad} · tocaba el {plan.proxima_fecha:%d/%m/%Y}", url)
        elif dias <= plan.dias_aviso:
            cuando = "hoy" if dias == 0 else f"en {_plural(dias, 'día', 'días')}"
            enviados += avisar(f"mantenimiento:{plan.pk}:{plan.proxima_fecha}:previo",
                               f"Mantenimiento {cuando}: {plan.equipo.codigo}",
                               f"{plan.actividad} · {plan.proxima_fecha:%d/%m/%Y}", url)
    return enviados


def revisar_respaldos(hoy=None) -> int:
    from . import respaldos

    hoy = hoy or timezone.localdate()
    estado = respaldos.estado(dias=7)
    url = reverse("ti:respaldos")
    enviados = 0
    fallo = estado["fallo_reciente"]
    if fallo:
        enviados += avisar(f"respaldo:fallo:{fallo['fecha'].isoformat()}", "Falló el respaldo automático",
                           f"{fallo['tipo_nombre']} · {fallo['fecha']:%d/%m/%Y %H:%M} · {fallo.get('mensaje', '')}", url)
    if estado["nivel"] in ("atrasado", "sin_datos"):
        detalle = (f"El último respaldo bueno es del {estado['ultimo_bueno']:%d/%m/%Y %H:%M}" if estado["ultimo_bueno"]
                   else "No hay ningún respaldo en la carpeta")
        enviados += avisar(f"respaldo:atrasado:{hoy}", "Los respaldos están atrasados", detalle, url)
    return enviados


def revisar_disco(hoy=None) -> int:
    from . import servidor

    hoy = hoy or timezone.localdate()
    url = reverse("ti:servidor")
    enviados = 0
    for d in servidor.discos():
        if d.nivel == "ok":
            continue
        # Crítico: una vez por día. Poco espacio: una vez por semana.
        periodo = hoy.isoformat() if d.nivel == "critico" else f"{hoy.isocalendar().year}-S{hoy.isocalendar().week}"
        titulo = f"{'Espacio crítico' if d.nivel == 'critico' else 'Poco espacio'} en el disco {d.unidad}"
        enviados += avisar(f"disco:{d.unidad}:{d.nivel}:{periodo}", titulo,
                           f"Quedan {d.libre / 1024 ** 3:.1f} GB libres ({d.porcentaje_libre:.0f} %) · {', '.join(d.usos)}", url)
    return enviados


def avisar_bloqueo(usuario: str, ip: str, *, minutos: int, intentos: int) -> bool:
    ahora = timezone.localtime()
    return avisar(f"bloqueo:{usuario}:{ahora:%Y%m%d%H%M}", f"Cuenta bloqueada: {usuario}",
                  f"{intentos} intentos fallidos desde {ip} · bloqueada {minutos} min", reverse("ti:accesos"))


def revisar(hoy=None) -> dict:
    """Corre todas las revisiones. Una que falla no impide las demás."""
    resultado = {}
    for nombre, funcion in (("licencias", revisar_licencias), ("mantenimientos", revisar_mantenimientos),
                            ("respaldos", revisar_respaldos), ("disco", revisar_disco)):
        try:
            with transaction.atomic():  # si una falla en la base, no arrastra la petición que la llamó
                resultado[nombre] = funcion(hoy)
        except Exception:  # noqa: BLE001
            logger.exception("Falló la revisión de avisos de TI: %s", nombre)
            resultado[nombre] = None
    return resultado


def revisar_si_toca() -> None:
    """Desde la consulta periódica de la campana: como mucho cada INTERVALO_REVISION segundos."""
    if not getattr(settings, "TI_ALERTAS_AUTOMATICAS", True):
        return
    if cache.add(_CLAVE_CACHE, True, INTERVALO_REVISION):
        revisar()
