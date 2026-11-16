"""
Accesos y bloqueos para TI › Accesos: intentos de ingreso fallidos y cuentas bloqueadas tras varios intentos.

Los datos salen del registro de seguridad (LOGS_DIR/seguridad.jsonl, ver apps.core.limite_peticiones): cada fallo,
cada bloqueo y cada desbloqueo hecho por TI queda en una línea. El bloqueo en sí vive en la caché en memoria,
así que una cuenta se considera bloqueada si su último bloqueo todavía no vence y TI no la desbloqueó después.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.core import limite_peticiones
from apps.core.models import Usuario

from . import registro_errores

logger = logging.getLogger("callflow.seguridad")


def duracion_bloqueo() -> timedelta:
    return timedelta(seconds=limite_peticiones._fallos_ingreso()[2])


def _eventos(desde) -> list[dict]:
    return sorted(registro_errores.lineas_seguridad(desde), key=lambda e: e["fecha"])


def cuentas_bloqueadas() -> list[dict]:
    """Cuentas con un bloqueo vigente, la que vence primero arriba."""
    ahora = timezone.localtime()
    bloqueo = duracion_bloqueo()
    vigentes: dict[str, dict] = {}
    for ev in _eventos(ahora - bloqueo):
        usuario = ev.get("usuario_intentado", "")
        if not usuario:
            continue
        if ev.get("evento") == "ingreso_bloqueado":
            vigentes[usuario] = {"usuario": usuario, "desde": ev["fecha"], "hasta": ev["fecha"] + bloqueo, "ip": ev.get("ip", "")}
        elif ev.get("evento") == "ingreso_desbloqueado":
            vigentes.pop(usuario, None)
    cuentas = {u.username: u for u in Usuario.objects.filter(username__in=vigentes)}
    resultado = []
    for usuario, datos in vigentes.items():
        if datos["hasta"] <= ahora:
            continue
        datos["cuenta"] = cuentas.get(usuario)
        datos["minutos"] = max(1, int((datos["hasta"] - ahora).total_seconds() // 60) + 1)
        resultado.append(datos)
    return sorted(resultado, key=lambda d: d["hasta"])


def intentos_fallidos(dias: int = 7) -> dict:
    """Fallos y bloqueos agrupados por usuario intentado y por IP."""
    desde = timezone.localtime() - timedelta(days=dias)
    por_usuario: dict[str, dict] = {}
    por_ip: dict[str, dict] = {}
    for ev in _eventos(desde):
        tipo = ev.get("evento")
        if tipo not in ("ingreso_fallido", "ingreso_bloqueado"):
            continue
        usuario, ip = ev.get("usuario_intentado", "") or "(vacío)", ev.get("ip", "") or "desconocida"
        u = por_usuario.setdefault(usuario, {"usuario": usuario, "fallos": 0, "bloqueos": 0, "ips": set(), "ultima": None})
        i = por_ip.setdefault(ip, {"ip": ip, "fallos": 0, "bloqueos": 0, "usuarios": set(), "ultima": None})
        for grupo in (u, i):
            grupo["ultima"] = ev["fecha"]
            if tipo == "ingreso_bloqueado":
                grupo["bloqueos"] += 1
            else:
                grupo["fallos"] += 1
        u["ips"].add(ip)
        i["usuarios"].add(usuario)
    cuentas = {c.username: c for c in Usuario.objects.filter(username__in=por_usuario)}
    for u in por_usuario.values():
        u["cuenta"] = cuentas.get(u["usuario"])  # None: probaron un usuario que no existe
    ordenar = lambda filas: sorted(filas, key=lambda f: (f["bloqueos"], f["fallos"], f["ultima"]), reverse=True)  # noqa: E731
    usuarios = ordenar(por_usuario.values())
    return {
        "por_usuario": usuarios, "por_ip": ordenar(por_ip.values()),
        "fallos": sum(u["fallos"] for u in usuarios), "bloqueos": sum(u["bloqueos"] for u in usuarios),
        "inexistentes": sum(1 for u in usuarios if u["cuenta"] is None),
    }


def desbloquear(usuario: str, por, request=None) -> None:
    """Quita el bloqueo (y los fallos acumulados) de una cuenta, y lo deja anotado en el registro de seguridad."""
    clave = limite_peticiones._clave_usuario(usuario)
    limite_peticiones.desbloquear_ingreso(clave)
    logger.warning("Cuenta desbloqueada por TI: %s (lo hizo %s)", clave, por.get_username(),
                   extra={"request": request, "evento": "ingreso_desbloqueado", "usuario_intentado": clave,
                          "ip": (request.META.get("REMOTE_ADDR") if request is not None else "") or ""})


def configuracion() -> dict:
    intentos, ventana, bloqueo = limite_peticiones._fallos_ingreso()
    return {"intentos": intentos, "ventana": ventana // 60, "bloqueo": bloqueo // 60,
            "activo": limite_peticiones.activo(), "por_ip": getattr(settings, "LIMITE_PETICIONES", {}).get("ingreso")}
