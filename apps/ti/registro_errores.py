"""
Registro de errores del sistema para la página «Servidor» de TI.

Cada error (nivel ERROR o más) se escribe como una línea JSON en LOGS_DIR/errores.jsonl, con rotación por
tamaño. Va a un archivo y no a la base de datos a propósito: así quedan registrados también los errores de
la propia base (caída, sin conexión, disco lleno), que son justo los que más interesa ver.

No se guardan el cuerpo de la petición, las contraseñas ni las variables locales: solo la ruta, el método,
el usuario y la traza del error.
"""
import hashlib
import json
import logging
import re
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

NOMBRE_ARCHIVO = "errores.jsonl"
MAX_TRAZA = 20_000


def carpeta_logs() -> Path:
    return Path(settings.LOGS_DIR)


class FormatoJSON(logging.Formatter):
    """Una línea JSON por error, con lo necesario para entenderlo sin datos sensibles."""

    def format(self, record: logging.LogRecord) -> str:
        datos = {
            "fecha": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds"),
            "nivel": record.levelname,
            "origen": record.name,
            "mensaje": record.getMessage()[:2000],
        }
        if record.exc_info:
            datos["excepcion"] = record.exc_info[0].__name__ if record.exc_info[0] else ""
            datos["traza"] = self.formatException(record.exc_info)[-MAX_TRAZA:]
        request = getattr(record, "request", None)
        if request is not None:
            datos["metodo"] = getattr(request, "method", "")
            datos["ruta"] = getattr(request, "path", "")[:300]
            datos["usuario"] = _usuario(request)
        if getattr(record, "status_code", None):
            datos["estado_http"] = record.status_code
        return json.dumps(datos, ensure_ascii=False)


def _usuario(request) -> str:
    # request.user puede consultar la base; si la base es la que falla, no debe tumbar el registro.
    try:
        usuario = request.user
        return usuario.get_username() if usuario.is_authenticated else ""
    except Exception:  # noqa: BLE001
        return ""


def _firma(error: dict) -> str:
    """Agrupa repeticiones del mismo error: origen + excepción + ruta (sin números) + última línea de la traza."""
    ruta = re.sub(r"\d+", "#", error.get("ruta", ""))
    ultima = (error.get("traza") or error.get("mensaje", "")).strip().splitlines()[-1:] or [""]
    clave = "|".join([error.get("origen", ""), error.get("excepcion", ""), ruta, ultima[0][:300]])
    return hashlib.sha1(clave.encode("utf-8")).hexdigest()[:12]


def _lineas_recientes(desde) -> list[dict]:
    """Errores desde `desde`, leyendo el archivo actual y los rotados (errores.jsonl.1, .2…)."""
    carpeta = carpeta_logs()
    archivos = sorted(carpeta.glob(NOMBRE_ARCHIVO + "*"), key=lambda p: p.stat().st_mtime) if carpeta.exists() else []
    errores = []
    for archivo in archivos:
        if datetime.fromtimestamp(archivo.stat().st_mtime).astimezone() < desde:
            continue  # todo lo de ese archivo es más viejo
        with archivo.open(encoding="utf-8", errors="replace") as f:
            for linea in f:
                try:
                    error = json.loads(linea)
                    error["fecha"] = datetime.fromisoformat(error["fecha"])
                except (ValueError, KeyError, TypeError):
                    continue  # línea cortada a medias o ajena al formato
                if error["fecha"] >= desde:
                    errores.append(error)
    return errores


def errores_recientes(dias: int = 7) -> list[dict]:
    """Errores agrupados (los iguales juntos), del más reciente al más viejo, con cuántas veces ocurrió cada uno."""
    desde = timezone.localtime() - timedelta(days=dias)
    grupos: "OrderedDict[str, dict]" = OrderedDict()
    for error in sorted(_lineas_recientes(desde), key=lambda e: e["fecha"], reverse=True):
        firma = _firma(error)
        if firma in grupos:
            grupo = grupos[firma]
            grupo["veces"] += 1
            grupo["primera"] = error["fecha"]
            if error.get("usuario"):
                grupo["usuarios"].add(error["usuario"])
        else:
            grupos[firma] = error | {"firma": firma, "veces": 1, "ultima": error["fecha"], "primera": error["fecha"],
                                     "usuarios": {error["usuario"]} if error.get("usuario") else set()}
    return list(grupos.values())


def contar_desde(horas: int = 24) -> int:
    return len(_lineas_recientes(timezone.localtime() - timedelta(hours=horas)))
