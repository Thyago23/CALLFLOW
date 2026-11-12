"""
Estado del servidor para TI: espacio en disco, tamaño de la base de datos y último respaldo.

Todo se lee en el momento (no se guarda nada). Solo usa la biblioteca estándar y consultas que el usuario
de la aplicación puede hacer sin ser superusuario de PostgreSQL.
"""
import os
import platform
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import django
from django.conf import settings
from django.db import connection
from django.utils import timezone

# Umbrales de alerta del disco (porcentaje libre).
LIBRE_CRITICO = 10
LIBRE_BAJO = 20
# Un respaldo con más de estos días se marca como atrasado.
DIAS_RESPALDO_ATRASADO = 2


@dataclass
class Disco:
    unidad: str
    usos: list[str]
    total: int
    usado: int
    libre: int

    @property
    def porcentaje_usado(self) -> float:
        return round(self.usado * 100 / self.total, 1) if self.total else 0

    @property
    def porcentaje_libre(self) -> float:
        return round(100 - self.porcentaje_usado, 1)

    @property
    def nivel(self) -> str:
        if self.porcentaje_libre < LIBRE_CRITICO:
            return "critico"
        if self.porcentaje_libre < LIBRE_BAJO:
            return "bajo"
        return "ok"


def _unidad(ruta: Path) -> str:
    """La unidad (Windows: C:\\) o el punto de montaje (Linux) donde está la ruta."""
    ruta = ruta.resolve()
    if os.name == "nt":
        return ruta.anchor or str(ruta)
    while not os.path.ismount(ruta) and ruta != ruta.parent:
        ruta = ruta.parent
    return str(ruta)


def discos() -> list[Disco]:
    """Una entrada por unidad, con lo que CALLFLOW guarda en ella (aplicación, archivos subidos, respaldos, registros)."""
    ubicaciones = [
        ("Aplicación", Path(settings.BASE_DIR)),
        ("Archivos subidos", Path(settings.MEDIA_ROOT)),
        ("Respaldos", Path(settings.CARPETA_RESPALDOS)),
        ("Registros de errores", Path(settings.LOGS_DIR)),
    ]
    por_unidad: dict[str, list[str]] = {}
    for uso, ruta in ubicaciones:
        existente = next((p for p in [ruta, *ruta.parents] if p.exists()), None)
        if existente is not None:
            por_unidad.setdefault(_unidad(existente), []).append(uso)
    resultado = []
    for unidad, usos in por_unidad.items():
        try:
            total, usado, libre = shutil.disk_usage(unidad)
        except OSError:
            continue
        resultado.append(Disco(unidad=unidad, usos=usos, total=total, usado=usado, libre=libre))
    return resultado


def tamano_carpeta(ruta: Path) -> tuple[int, int]:
    """(bytes, archivos) de una carpeta. Sin seguir enlaces."""
    total = cantidad = 0
    if not ruta.exists():
        return 0, 0
    for raiz, _, archivos in os.walk(ruta):
        for nombre in archivos:
            try:
                total += os.lstat(os.path.join(raiz, nombre)).st_size
                cantidad += 1
            except OSError:
                pass
    return total, cantidad


def base_de_datos(cuantas_tablas: int = 10) -> dict:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), pg_database_size(current_database()), "
                       "current_setting('server_version'), current_setting('max_connections')::int, "
                       "now() - pg_postmaster_start_time()")
        nombre, tamano, version, max_conexiones, encendida = cursor.fetchone()
        cursor.execute("SELECT state, count(*) FROM pg_stat_activity WHERE datname = current_database() GROUP BY state")
        estados = {estado or "otro": n for estado, n in cursor.fetchall()}
        cursor.execute("""
            SELECT c.relname, pg_total_relation_size(c.oid), pg_relation_size(c.oid), c.reltuples::bigint
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p') AND n.nspname = 'public'
            ORDER BY pg_total_relation_size(c.oid) DESC LIMIT %s
        """, [cuantas_tablas])
        tablas = [{"nombre": t, "total": total, "datos": datos, "indices": total - datos, "filas": max(filas, 0)}
                  for t, total, datos, filas in cursor.fetchall()]
    conexiones = sum(estados.values())
    return {
        "nombre": nombre, "tamano": tamano, "version": version, "encendida": encendida,
        "conexiones": conexiones, "activas": estados.get("active", 0), "max_conexiones": max_conexiones,
        "porcentaje_conexiones": round(conexiones * 100 / max_conexiones, 1) if max_conexiones else 0,
        "tablas": tablas,
    }


def ultimo_respaldo() -> dict:
    carpeta = Path(settings.CARPETA_RESPALDOS)
    copias = sorted(carpeta.glob("callflow_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True) if carpeta.exists() else []
    if not copias:
        return {"existe": False, "carpeta": str(carpeta)}
    ultimo = copias[0]
    fecha = timezone.make_aware(datetime.fromtimestamp(ultimo.stat().st_mtime))
    dias = (timezone.localtime() - fecha).days
    return {"existe": True, "carpeta": str(carpeta), "archivo": ultimo.name, "fecha": fecha, "tamano": ultimo.stat().st_size,
            "cantidad": len(copias), "total": sum(p.stat().st_size for p in copias), "dias": dias,
            "atrasado": dias >= DIAS_RESPALDO_ATRASADO}


def _nombre_sistema() -> str:
    nombre, version = platform.system(), platform.release()
    # Windows 11 se presenta como «10»: se distingue por el número de compilación (22000 en adelante).
    if nombre == "Windows" and version == "10":
        try:
            if int(platform.version().split(".")[-1]) >= 22000:
                version = "11"
        except ValueError:
            pass
    return f"{nombre} {version}"


def sistema() -> dict:
    return {"equipo": platform.node(), "sistema": _nombre_sistema(),
            "python": platform.python_version(), "django": django.get_version(), "depuracion": settings.DEBUG}
