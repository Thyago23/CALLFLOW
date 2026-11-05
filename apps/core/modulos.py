"""
Catálogo de módulos de CALLFLOW (secciones 4.1 a 4.5 de la propuesta).

Alimenta el menú lateral y las tarjetas del dashboard. Cuando se termina un módulo,
basta con poner su `url` (nombre de ruta de Django) para que aparezca como disponible.
"""
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class Modulo:
    clave: str
    nombre: str
    seccion: str
    icono: str  # nombre de Bootstrap Icons, sin el prefijo "bi-"
    permiso: str
    descripcion: str
    url: str | None = None  # None = todavía no implementado

    @property
    def disponible(self) -> bool:
        return self.url is not None

    @property
    def href(self) -> str:
        try:
            return reverse(self.url) if self.url else ""
        except NoReverseMatch:
            return ""


SECCIONES = [
    ("4.1", "Administración"),
    ("4.2", "Nómina"),
    ("4.3", "Ventas"),
    ("4.4", "Cierre de ventas"),
    ("4.5", "Reportes"),
]

MODULOS = [
    Modulo("personal", "Personal", "4.1", "people", "personal.view_empleado",
           "Registro de empleados, salarios, comisiones y novedades.", url="personal:empleado_lista"),
    Modulo("asistencia", "Asistencia", "4.1", "clock-history", "asistencia.view_registroasistencia",
           "Entradas, salidas, atrasos y ausencias.", url="asistencia:tablero"),
    Modulo("documentos", "Documentos", "4.1", "folder2-open", "documentos.view_documento",
           "Expedientes, archivos adjuntos y contratos desde plantillas."),
    Modulo("inventario", "Inventario", "4.1", "box-seam", "inventario.view_material",
           "Materiales de oficina, existencias y movimientos."),
    Modulo("agenda", "Agenda", "4.1", "calendar3", "agenda.view_evento",
           "Actividades, reuniones y recordatorios."),
    Modulo("nomina", "Nómina", "4.2", "cash-coin", "nomina.view_periodonomina",
           "Periodos, cálculo, liquidaciones y solicitudes de pago."),
    Modulo("ventas", "Ventas", "4.3", "telephone-outbound", "ventas.view_formulariodami",
           "Formularios DAMI, hoja de producción y bases comerciales."),
    Modulo("cierre", "Cierre de ventas", "4.4", "check2-circle", "cierre.view_gestioncierre",
           "Gestión de cierres, metas mensuales y panel comercial."),
    Modulo("reportes", "Reportes", "4.5", "file-earmark-bar-graph", "reportes.view_tiporeporte",
           "Reportes en PDF con filtros por fechas, empleados y periodos."),
]


def modulos_de(usuario) -> list[Modulo]:
    """Módulos que el usuario puede ver según su rol."""
    if not usuario.is_authenticated:
        return []
    return [m for m in MODULOS if usuario.has_perm(m.permiso)]


def menu_de(usuario) -> list[dict]:
    """Módulos agrupados por sección, solo las secciones con algo visible."""
    visibles = modulos_de(usuario)
    return [
        {"codigo": codigo, "nombre": nombre, "modulos": [m for m in visibles if m.seccion == codigo]}
        for codigo, nombre in SECCIONES
        if any(m.seccion == codigo for m in visibles)
    ]
