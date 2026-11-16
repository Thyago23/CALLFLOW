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
    exclusivo: bool = False  # solo con el permiso asignado directamente a la cuenta (ni superusuario ni grupos)
    roles: tuple = ()  # si se indica, solo esos roles lo ven (ni el superusuario): p. ej. el módulo de TI
    # Rutas (namespace:nombre) en las que el enlace del menú queda marcado. Vacío = todo el namespace `clave`.
    # Sirve cuando un mismo namespace se reparte en varias entradas del menú (TI).
    vistas: tuple = ()

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
    ("TI", "Tecnología"),
]

MODULOS = [
    Modulo("personal", "Personal", "4.1", "people", "personal.view_empleado",
           "Registro de empleados, salarios, comisiones y novedades.", url="personal:empleado_lista"),
    Modulo("asistencia", "Asistencia", "4.1", "clock-history", "asistencia.view_registroasistencia",
           "Entradas, salidas, atrasos y ausencias.", url="asistencia:tablero"),
    Modulo("documentos", "Documentos", "4.1", "folder2-open", "documentos.view_documento",
           "Expedientes, archivos adjuntos y contratos desde plantillas.", url="documentos:plantillas"),
    Modulo("inventario", "Inventario", "4.1", "box-seam", "inventario.view_material",
           "Materiales de oficina, existencias y movimientos.", url="inventario:materiales"),
    Modulo("agenda", "Agenda", "4.1", "calendar3", "agenda.view_evento",
           "Actividades, reuniones y recordatorios.", url="agenda:calendario"),
    Modulo("nomina", "Nómina", "4.2", "cash-coin", "nomina.view_periodonomina",
           "Roles de pago, reportes para imprimir y caja chica.", url="nomina:resumen"),
    Modulo("ventas", "Ventas", "4.3", "telephone-outbound", "ventas.view_formulariodami",
           "Formularios DAMI, hoja de producción y bases comerciales.", url="ventas:inicio"),
    Modulo("cierre", "Cierre de ventas", "4.4", "check2-circle", "cierre.view_gestioncierre",
           "Gestión de cierres, metas mensuales y panel comercial.", url="cierre:bandeja"),
    Modulo("tablero", "Tablero de ventas", "4.4", "trophy", "cierre.view_metamensual",
           "Lo vendido por cada vendedor en el mes, para proyectar en la TV del call.", url="tablero:ventas"),
    Modulo("chips", "Registro de chips", "4.1", "sim", "chips.gestionar_chips",
           "Chips del call, reposiciones y cambios de número, con reporte para firmar.", url="chips:lista", exclusivo=True),
    Modulo("breaks", "Breaks", "4.1", "cup-straw", "asistencia.gestionar_breaks",
           "Dar breaks a los vendedores y ver quién volvió tarde.", url="asistencia:breaks", exclusivo=True),
    # TI: un panel con lo que necesita atención y tres secciones (mesa de ayuda, equipos, monitoreo).
    Modulo("ti", "Panel de TI", "TI", "speedometer2", "",
           "Lo que necesita atención hoy: reportes, mantenimientos, licencias, respaldos y servidor.", url="ti:panel",
           roles=("TI",), vistas=("ti:panel",)),
    Modulo("soporte_ti", "Mesa de ayuda", "TI", "headset", "",
           "Reportes de fallas de la empresa: atenderlos a tiempo.", url="soporte:inicio", roles=("TI",),
           vistas=("soporte:inicio", "soporte:crear", "soporte:detalle")),
    Modulo("ti_equipos", "Equipos y licencias", "TI", "pc-display", "",
           "Inventario, mantenimiento preventivo, préstamos con acta y licencias.", url="ti:equipos", roles=("TI",),
           vistas=("ti:equipos", "ti:crear", "ti:detalle", "ti:editar", "ti:evento", "ti:entregar", "ti:devolver",
                   "ti:prestamos", "ti:acta_pdf", "ti:acta_firmada", "ti:acta_firmada_subir", "ti:mantenimiento",
                   "ti:plan_crear", "ti:plan_editar", "ti:plan_realizado", "ti:licencias", "ti:licencia_crear",
                   "ti:licencia_editar")),
    Modulo("ti_monitoreo", "Monitoreo", "TI", "activity", "",
           "Quién está conectado, accesos y bloqueos, respaldos y estado del servidor.", url="ti:conectados", roles=("TI",),
           vistas=("ti:conectados", "ti:cerrar_sesion", "ti:accesos", "ti:desbloquear", "ti:respaldos", "ti:servidor")),
    Modulo("soporte", "Mesa de ayuda", "TI", "headset", "",
           "Reporte una falla (audio, internet, computador, sistema) y siga su atención.", url="soporte:inicio",
           roles=("ADMINISTRADOR", "VENDEDOR", "ENCARGADO_CIERRE")),
    Modulo("mensajes", "Mensajes", "TI", "chat-dots", "",
           "Mensajería interna entre TI, administradores y supervisores.", url="mensajes:bandeja",
           roles=("ADMINISTRADOR", "ENCARGADO_CIERRE", "TI")),
    Modulo("reportes", "Reportes", "4.5", "file-earmark-bar-graph", "reportes.view_tiporeporte",
           "Reportes en PDF, estadísticas y proyección de ventas.", url="reportes:centro"),
]


def modulos_de(usuario) -> list[Modulo]:
    """Módulos que el usuario puede ver según su rol."""
    if not usuario.is_authenticated:
        return []
    from .acceso import tiene_permiso_directo

    def ve(m):
        if m.roles:
            return usuario.rol in m.roles
        if usuario.rol in ("TI",) and m.clave != "reportes":
            return False  # TI no ve los módulos de la empresa, aunque se le diera algún permiso
        return tiene_permiso_directo(usuario, m.permiso) if m.exclusivo else usuario.has_perm(m.permiso)

    return [m for m in MODULOS if ve(m)]


# Cómo se agrupan los módulos en el menú y en el inicio: por lo que hace cada uno, no por la
# sección de la propuesta (la agenda la usan todos los roles, no solo administración).
GRUPOS = [
    ("Administración", ("personal", "asistencia", "documentos", "inventario", "nomina")),
    ("Comercial", ("ventas", "cierre", "tablero", "breaks", "chips")),
    ("Tecnología", ("ti", "soporte_ti", "ti_equipos", "ti_monitoreo")),
    ("Herramientas", ("soporte", "mensajes", "agenda", "reportes")),
]


def grupos_de(usuario, solo_disponibles: bool = False) -> list[dict]:
    """Módulos visibles agrupados; solo los grupos que tienen algo que mostrar."""
    visibles = {m.clave: m for m in modulos_de(usuario) if m.disponible or not solo_disponibles}
    grupos = [{"nombre": nombre, "modulos": [visibles[c] for c in claves if c in visibles]} for nombre, claves in GRUPOS]
    return [g for g in grupos if g["modulos"]]


def menu_de(usuario) -> list[dict]:
    """Menú lateral: solo lo que ya se puede usar."""
    return grupos_de(usuario, solo_disponibles=True)
