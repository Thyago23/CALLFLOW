"""
Límite de peticiones (rate limit) para que nadie sature CALLFLOW a fuerza de peticiones.

Cuenta las peticiones de cada IP en ventanas fijas de tiempo, en la caché en memoria (no toca la base de datos).
El middleware va antes de las sesiones a propósito: una petición rechazada no hace ninguna consulta a PostgreSQL,
que es justo lo que se quiere proteger cuando alguien bombardea el servidor.

Reglas (ajustables en settings.LIMITE_PETICIONES):
- «ingreso»: intentos de inicio de sesión (POST al login) por IP. Frena que prueben códigos de acceso a la fuerza.
- «sondeo»: las consultas automáticas cada pocos segundos (contadores de la campana, chat, breaks, tablero).
  Con el uso normal una pestaña hace ~12–20 por minuto; el límite deja varias pestañas abiertas sin problema.
- «general»: todo lo demás.

Además, cada usuario tiene un tope de intentos fallidos de ingreso: después de varios errores seguidos esa cuenta
queda bloqueada unos minutos aunque prueben desde otra IP (ver `ingreso_bloqueado` y `registrar_ingreso_fallido`).

Con varios procesos (Gunicorn) cada proceso lleva su propia cuenta, así que el tope real es el configurado por la
cantidad de procesos; para un ataque sigue siendo un freno. Los archivos estáticos no se cuentan.
"""
import logging
import time

from django.conf import settings
from django.core.cache import caches
from django.http import HttpResponse
from django.urls import reverse

from apps.ti.servicios import ip_de

logger = logging.getLogger("callflow.seguridad")

# (máximo de peticiones, ventana en segundos)
LIMITES_POR_DEFECTO = {
    "ingreso": (10, 300),
    "sondeo": (120, 60),
    "general": (300, 60),
}
# Fallos de ingreso por usuario: cuántos, en qué ventana (s) y cuánto dura el bloqueo (s).
FALLOS_INGRESO_POR_DEFECTO = (5, 900, 900)

# Rutas de consulta automática (HTMX cada pocos segundos). Se comparan por prefijo o por terminación.
_SONDEO_TERMINA = ("/nuevos/", "/escribiendo/", "/breaks/panel/")
_SONDEO_EMPIEZA = ("/notificaciones/contadores/", "/notificaciones/panel/", "/mensajes/panel/", "/tablero/")

_PAGINA_429 = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Demasiadas solicitudes · CALLFLOW</title>
<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#212529}
@media (prefers-color-scheme:dark){body{background:#212529;color:#dee2e6}}</style></head><body>
<h1>Demasiadas solicitudes</h1>
<p>Se recibieron demasiadas solicitudes desde este equipo en poco tiempo. Espere __SEGUNDOS__ segundos y vuelva a intentarlo.</p>
<p>Si el problema continúa, comuníquese con el área de TI.</p></body></html>"""


def _cache():
    return caches[getattr(settings, "LIMITE_PETICIONES_CACHE", "default")]


def activo() -> bool:
    return getattr(settings, "LIMITE_PETICIONES_ACTIVO", True)


def _limites() -> dict:
    return LIMITES_POR_DEFECTO | getattr(settings, "LIMITE_PETICIONES", {})


def contar(clave: str, maximo: int, ventana: int) -> tuple[bool, int]:
    """
    Suma una petición a `clave` en la ventana actual. Devuelve (excedido, segundos hasta que se libera).
    Ventana fija: el contador empieza en la primera petición y caduca solo.
    """
    cache = _cache()
    inicio = int(time.time()) // ventana * ventana
    clave = f"limite:{clave}:{inicio}"
    if cache.add(clave, 1, ventana + 5):
        cuenta = 1
    else:
        try:
            cuenta = cache.incr(clave)
        except ValueError:  # caducó justo entre add e incr
            cache.add(clave, 1, ventana + 5)
            cuenta = 1
    return cuenta > maximo, max(1, inicio + ventana - int(time.time()))


def regla_de(request) -> str | None:
    ruta = request.path
    if ruta.startswith("/" + settings.STATIC_URL.lstrip("/")) or ruta == "/sw.js":
        return None
    if request.method == "POST" and ruta in (reverse("login"), reverse("admin:login")):
        return "ingreso"
    if ruta.startswith(_SONDEO_EMPIEZA) or ruta.endswith(_SONDEO_TERMINA):
        return "sondeo"
    return "general"


def _respuesta_429(request, segundos: int) -> HttpResponse:
    if request.headers.get("HX-Request"):
        # HTMX no reemplaza el contenido con una respuesta de error: la página queda como estaba.
        respuesta = HttpResponse(status=429)
    else:
        respuesta = HttpResponse(_PAGINA_429.replace("__SEGUNDOS__", str(segundos)), status=429, content_type="text/html; charset=utf-8")
    respuesta["Retry-After"] = str(segundos)
    return respuesta


class LimitePeticionesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if activo():
            regla = regla_de(request)
            if regla:
                ip = ip_de(request) or "desconocida"
                maximo, ventana = _limites()[regla]
                clave = f"{regla}:{ip}"
                excedido, segundos = contar(clave, maximo, ventana)
                if excedido:
                    if _cache().add(f"limite-aviso:{clave}", 1, ventana):
                        logger.warning("Límite de peticiones superado (%s): %s más de %s en %s s", regla, ip, maximo, ventana,
                                       extra={"request": request, "evento": "limite_peticiones", "ip": ip, "status_code": 429})
                    return _respuesta_429(request, segundos)
        return self.get_response(request)


# --------------------------------------------------------------------------- fallos de ingreso por usuario
def _fallos_ingreso() -> tuple[int, int, int]:
    return getattr(settings, "LIMITE_FALLOS_INGRESO", FALLOS_INGRESO_POR_DEFECTO)


def _clave_usuario(usuario: str) -> str:
    from .codigos import normalizar_usuario

    return (normalizar_usuario(usuario or "") or (usuario or "").strip().lower())[:150]


def ingreso_bloqueado(usuario: str) -> int:
    """Segundos que le faltan al bloqueo de esa cuenta (0 = puede intentar)."""
    if not activo():
        return 0
    hasta = _cache().get(f"ingreso-bloqueo:{_clave_usuario(usuario)}")
    return max(0, int(hasta - time.time())) if hasta else 0


def registrar_ingreso_fallido(usuario: str, request=None) -> None:
    clave = _clave_usuario(usuario)
    if not clave or not activo():
        return
    maximo, ventana, bloqueo = _fallos_ingreso()
    cache = _cache()
    contador = f"ingreso-fallos:{clave}"
    if cache.add(contador, 1, ventana):
        fallos = 1
    else:
        try:
            fallos = cache.incr(contador)
        except ValueError:
            cache.add(contador, 1, ventana)
            fallos = 1
    ip = (ip_de(request) if request is not None else None) or "desconocida"
    extra = {"request": request, "evento": "ingreso_fallido", "ip": ip, "usuario_intentado": clave}
    if fallos >= maximo:
        cache.set(f"ingreso-bloqueo:{clave}", time.time() + bloqueo, bloqueo)
        cache.delete(contador)
        logger.warning("Cuenta bloqueada %s min por %s intentos fallidos: %s (IP %s)", bloqueo // 60, fallos, clave, ip,
                       extra=extra | {"evento": "ingreso_bloqueado"})
    else:
        logger.warning("Ingreso fallido: %s (IP %s)", clave, ip, extra=extra)


def limpiar_fallos_ingreso(usuario: str) -> None:
    _cache().delete(f"ingreso-fallos:{_clave_usuario(usuario)}")


# Señales de Django: cubren cualquier puerta de ingreso (la pantalla de CALLFLOW y la de /admin/).
# Se conectan en CoreConfig.ready(). El bloqueo en sí lo aplica el backend de autenticación.
def al_fallar_ingreso(sender, credentials, request=None, **kwargs):
    registrar_ingreso_fallido(credentials.get("username", ""), request)


def al_ingresar(sender, request, user, **kwargs):
    limpiar_fallos_ingreso(user.get_username())
