"""
Historial de respaldos para TI › Respaldos.

scripts/respaldar_bd.ps1 escribe una línea JSON por cada corrida en LOGS_DIR/respaldos.jsonl, salga bien o mal:
fecha, tipo (diario u hora), resultado (ok o fallo), archivo, tamaño, duración, copia al segundo destino y el mensaje
de error. Va a un archivo y no a la base a propósito: un respaldo que falla porque la base no responde también
tiene que quedar anotado.

Para que la página lo vea, LOGS_DIR y CARPETA_RESPALDOS del servidor web tienen que ser las mismas carpetas que usa
el script (en la PC que tiene la base).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import servidor

NOMBRE_ARCHIVO = "respaldos.jsonl"
TIPOS = {"diario": "Diario (base + archivos)", "hora": "Cada hora (solo la base)"}


def archivo() -> Path:
    return Path(settings.LOGS_DIR) / NOMBRE_ARCHIVO


def historial(dias: int = 30) -> list[dict]:
    """Corridas de los últimos `dias` días, de la más reciente a la más vieja."""
    ruta = archivo()
    if not ruta.exists():
        return []
    desde = timezone.localtime() - timedelta(days=dias)
    corridas = []
    with ruta.open(encoding="utf-8-sig", errors="replace") as f:
        for linea in f:
            try:
                corrida = json.loads(linea)
                corrida["fecha"] = datetime.fromisoformat(corrida["fecha"])
            except (ValueError, KeyError, TypeError):
                continue
            if corrida["fecha"] < desde:
                continue
            corrida["ok"] = corrida.get("resultado") == "ok"
            corrida["tipo_nombre"] = TIPOS.get(corrida.get("tipo", ""), corrida.get("tipo", ""))
            corridas.append(corrida)
    return sorted(corridas, key=lambda c: c["fecha"], reverse=True)


def estado(dias: int = 30) -> dict:
    """
    Resumen para la página y los avisos:
    - nivel «fallo»: la última corrida de algún tipo falló;
    - «atrasado»: el último respaldo bueno tiene más de servidor.DIAS_RESPALDO_ATRASADO días;
    - «sin_datos»: no hay respaldos;
    - «ok».
    """
    corridas = historial(dias)
    por_tipo = {}
    for tipo in TIPOS:
        del_tipo = [c for c in corridas if c.get("tipo") == tipo]
        por_tipo[tipo] = {
            "nombre": TIPOS[tipo],
            "ultima": del_tipo[0] if del_tipo else None,
            "ultimo_ok": next((c for c in del_tipo if c["ok"]), None),
            "fallos": sum(1 for c in del_tipo if not c["ok"]),
            "total": len(del_tipo),
        }
    archivo_ultimo = servidor.ultimo_respaldo()  # lo que hay en la carpeta (también respaldos de antes del historial)
    buenos = [t["ultimo_ok"]["fecha"] for t in por_tipo.values() if t["ultimo_ok"]]
    if archivo_ultimo.get("existe"):
        buenos.append(archivo_ultimo["fecha"])
    ultimo_bueno = max(buenos) if buenos else None
    fallidas = [t["ultima"] for t in por_tipo.values() if t["ultima"] and not t["ultima"]["ok"]]
    if fallidas:
        nivel = "fallo"
    elif ultimo_bueno is None:
        nivel = "sin_datos"
    elif (timezone.localtime() - ultimo_bueno).days >= servidor.DIAS_RESPALDO_ATRASADO:
        nivel = "atrasado"
    else:
        nivel = "ok"
    return {
        "nivel": nivel, "por_tipo": por_tipo, "ultimo_bueno": ultimo_bueno,
        "fallo_reciente": max(fallidas, key=lambda c: c["fecha"]) if fallidas else None,
        "corridas": corridas, "fallos": sum(1 for c in corridas if not c["ok"]), "carpeta": archivo_ultimo,
        "dias_atrasado": servidor.DIAS_RESPALDO_ATRASADO, "hay_historial": archivo().exists(),
    }


def copias_en_carpeta(cuantas: int = 15) -> list[dict]:
    carpeta = Path(settings.CARPETA_RESPALDOS)
    if not carpeta.exists():
        return []
    archivos = [p for p in carpeta.iterdir() if p.is_file() and p.name.startswith("callflow_") and p.suffix in (".dump", ".zip")]
    archivos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"nombre": p.name, "tamano": p.stat().st_size,
             "fecha": timezone.make_aware(datetime.fromtimestamp(p.stat().st_mtime)),
             "tipo": "Archivos subidos" if p.suffix == ".zip" else ("Cada hora" if "_hora_" in p.name else "Diario")}
            for p in archivos[:cuantas]]
