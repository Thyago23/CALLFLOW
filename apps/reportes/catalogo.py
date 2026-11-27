"""
4.5 Reportes y exportación (SCRUM-61 a SCRUM-66): qué reportes existen y cómo se arma cada uno.

Cada reporte devuelve una `Tabla` (columnas, filas, totales y notas) que se muestra en
pantalla y se exporta a PDF con el mismo contenido. Quién puede generarlo lo dice la tabla
reportes_tiporeporte (roles permitidos), que se carga con `inicializar_datos` a partir de
TIPOS_REPORTE.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core.models import Rol

from .formato import NUMERICOS, formatear

A, V, C, T, K = Rol.ADMINISTRADOR, Rol.VENDEDOR, Rol.ENCARGADO_CIERRE, Rol.TI, Rol.CONTADOR


# --------------------------------------------------------------------------- estructura común
@dataclass
class Columna:
    titulo: str
    tipo: str = "texto"  # texto | dinero | entero | cantidad | porcentaje | fecha
    peso: float = 1.0  # ancho relativo en el PDF

    @property
    def numerica(self) -> bool:
        return self.tipo in NUMERICOS


@dataclass
class Tabla:
    """Resultado de un reporte listo para pantalla y PDF: columnas, filas, totales y notas."""
    columnas: list
    filas: list
    periodo: str = ""
    totales: list | None = None
    notas: list = field(default_factory=list)
    horizontal: bool = False
    vacio: str = "No hay datos para los filtros elegidos."
    vista: str = ""  # plantilla con una vista visual que se muestra en pantalla antes de la tabla
    extra: dict = field(default_factory=dict)  # datos para esa vista

    def celdas(self, fila, vacio="—"):
        return [(formatear(v, c.tipo, vacio), c.numerica) for v, c in zip(fila, self.columnas)]

    def filas_formateadas(self, vacio="—"):
        return [self.celdas(f, vacio) for f in self.filas]

    def totales_formateados(self, vacio=""):
        return self.celdas(self.totales, vacio) if self.totales else None


@dataclass(frozen=True)
class Reporte:
    """Definición de un reporte del catálogo: filtros que pide, quién lo ve y cómo se arma."""
    codigo: str
    nombre: str
    modulo: str
    descripcion: str
    filtros: tuple
    roles: tuple
    obligatorios: tuple = ()
    estados: tuple = ()  # opciones del filtro «estado»
    icono: str = "file-earmark-text"

    def tabla(self, f: dict, usuario) -> Tabla:
        return _CONSTRUCTORES[self.codigo](f, usuario)

    def pdf(self, f: dict, usuario, filtros_texto: str) -> bytes:
        if self.codigo in _DOCUMENTOS:
            return _DOCUMENTOS[self.codigo](f)
        from .pdf import tabla_pdf

        return tabla_pdf(self.nombre, self.tabla(f, usuario), filtros_texto, usuario)


def _periodo_rango(f) -> str:
    return f"Del {f['desde']:%d/%m/%Y} al {f['hasta']:%d/%m/%Y}"


def _periodo_mes(f) -> str:
    from apps.nomina.calculo import nombre_mes

    return f"Periodo: {nombre_mes(*f['mes'])}"


def _nombre(empleado) -> str:
    return f"{empleado.apellidos} {empleado.nombres}"


def equipos_al(fecha: date) -> dict:
    """{empleado_id: esquema de comisión vigente en esa fecha}."""
    from apps.personal.models import AsignacionComision

    vigentes = (AsignacionComision.objects.filter(vigente_desde__lte=fecha)
                .filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=fecha)).select_related("esquema"))
    return {a.empleado_id: a.esquema for a in vigentes}


# --------------------------------------------------------------------------- administrativos (SCRUM-64)
def _personal(f, usuario) -> Tabla:
    from apps.documentos.contratos import salario_vigente
    from apps.personal.models import Empleado

    qs = Empleado.objects.select_related("cargo").order_by("apellidos", "nombres")
    if f.get("estado"):
        qs = qs.filter(estado=f["estado"])
    if f.get("cargo"):
        qs = qs.filter(cargo=f["cargo"])
    hoy = timezone.localdate()
    filas = [[e.identificacion, _nombre(e), str(e.cargo), e.get_tipo_contrato_display(), e.fecha_ingreso,
              salario_vigente(e, hoy), e.get_estado_display()] for e in qs]
    total_sueldos = sum((fila[5] or Decimal("0") for fila in filas), Decimal("0"))
    return Tabla(
        columnas=[Columna("Identificación", peso=1.2), Columna("Apellidos y nombres", peso=2.6), Columna("Cargo", peso=1.8),
                  Columna("Contrato", peso=1.3), Columna("Ingreso", "fecha"), Columna("Sueldo vigente", "dinero", 1.1),
                  Columna("Estado", peso=1.1)],
        filas=filas, periodo=f"Al {hoy:%d/%m/%Y}", horizontal=True,
        totales=["", f"{len(filas)} empleado{'s' if len(filas) != 1 else ''}", "", "", "", total_sueldos, ""],
    )


def _asistencia(f, usuario) -> Tabla:
    """Asistencia por empleado en el rango: días trabajados, atrasos, ausencias y salidas anticipadas."""
    from apps.asistencia.models import NovedadAsistencia, RegistroAsistencia
    from apps.personal.models import Empleado

    desde, hasta = f["desde"], f["hasta"]
    empleados = (Empleado.objects.select_related("cargo").filter(fecha_ingreso__lte=hasta)
                 .filter(Q(fecha_salida__isnull=True) | Q(fecha_salida__gte=desde)).order_by("apellidos", "nombres"))
    if f.get("empleado"):
        empleados = empleados.filter(pk=f["empleado"].pk)
    dias = dict(RegistroAsistencia.objects.filter(fecha__range=(desde, hasta)).values_list("empleado")
                .annotate(n=Count("fecha", distinct=True)))
    T, E = NovedadAsistencia.Tipo, NovedadAsistencia.Estado
    novedades = {n["empleado"]: n for n in NovedadAsistencia.objects.filter(fecha__range=(desde, hasta)).values("empleado").annotate(
        faltas_j=Count("pk", filter=Q(tipo=T.AUSENCIA, estado=E.JUSTIFICADA)),
        faltas_i=Count("pk", filter=Q(tipo=T.AUSENCIA, estado=E.NO_JUSTIFICADA)),
        atrasos=Count("pk", filter=Q(tipo=T.ATRASO) & ~Q(estado=E.JUSTIFICADA)),
        minutos=Sum("minutos", filter=Q(tipo=T.ATRASO) & ~Q(estado=E.JUSTIFICADA)),
        salidas=Count("pk", filter=Q(tipo=T.SALIDA_ANTICIPADA) & ~Q(estado=E.JUSTIFICADA)),
        pendientes=Count("pk", filter=Q(estado=E.PENDIENTE)),
    )}
    filas = []
    for e in empleados:
        n = novedades.get(e.pk, {})
        filas.append([_nombre(e), str(e.cargo), dias.get(e.pk, 0), n.get("faltas_j", 0), n.get("faltas_i", 0),
                      n.get("atrasos", 0), n.get("minutos") or 0, n.get("salidas", 0), n.get("pendientes", 0)])
    sumas = [sum(fila[i] for fila in filas) for i in range(2, 9)]
    return Tabla(
        columnas=[Columna("Empleado", peso=2.6), Columna("Cargo", peso=1.7), Columna("Días con marcación", "entero"),
                  Columna("Faltas justificadas", "entero"), Columna("Faltas injustificadas", "entero"), Columna("Atrasos", "entero", .9),
                  Columna("Minutos de atraso", "entero"), Columna("Salidas anticipadas", "entero"), Columna("Por revisar", "entero", .9)],
        filas=filas, periodo=_periodo_rango(f), horizontal=True, totales=["Totales", ""] + sumas,
        notas=["«Por revisar» son faltas o atrasos que aún no se clasifican como justificados o no justificados."],
    )


def _inventario(f, usuario) -> Tabla:
    """Existencias de materiales activos, con opción de ver solo los de stock bajo."""
    from django.db.models import F

    from apps.inventario.models import Material

    qs = Material.objects.filter(activo=True).select_related("categoria").order_by("categoria__nombre", "nombre")
    if f.get("categoria"):
        qs = qs.filter(categoria=f["categoria"])
    if f.get("estado") == "BAJO":
        qs = qs.filter(stock_actual__lte=F("stock_minimo"))
    filas = [[m.codigo, m.nombre, str(m.categoria), m.get_unidad_medida_display(), m.stock_actual, m.stock_minimo,
              "Stock bajo" if m.stock_actual <= m.stock_minimo else "Normal"] for m in qs]
    bajos = sum(1 for fila in filas if fila[6] == "Stock bajo")
    return Tabla(
        columnas=[Columna("Código", peso=.9), Columna("Material", peso=2.4), Columna("Categoría", peso=1.5), Columna("Unidad", peso=.9),
                  Columna("Stock actual", "cantidad"), Columna("Stock mínimo", "cantidad"), Columna("Estado")],
        filas=filas, periodo=f"Al {timezone.localdate():%d/%m/%Y}",
        totales=["", f"{len(filas)} materiales", "", "", None, None, f"{bajos} con stock bajo"],
    )


def _movimientos(f, usuario) -> Tabla:
    """Movimientos de inventario del rango, con totales por tipo."""
    from apps.inventario.models import MovimientoInventario

    qs = (MovimientoInventario.objects.filter(fecha__date__range=(f["desde"], f["hasta"]))
          .select_related("material", "responsable").order_by("fecha", "pk"))
    if f.get("categoria"):
        qs = qs.filter(material__categoria=f["categoria"])
    filas = [[m.fecha, m.material.codigo, m.material.nombre, m.get_tipo_display(), m.cantidad, m.stock_resultante, m.motivo,
              (m.responsable.get_full_name() or m.responsable.username) if m.responsable else ""] for m in qs]
    por_tipo = defaultdict(int)
    for fila in filas:
        por_tipo[fila[3]] += 1
    return Tabla(
        columnas=[Columna("Fecha", "fecha", 1.3), Columna("Código", peso=.8), Columna("Material", peso=2.1), Columna("Tipo", peso=1.2),
                  Columna("Cantidad", "cantidad", .9), Columna("Stock resultante", "cantidad"), Columna("Motivo", peso=2.2),
                  Columna("Responsable", peso=1.5)],
        filas=filas, periodo=_periodo_rango(f), horizontal=True,
        notas=[" · ".join(f"{tipo}: {n}" for tipo, n in sorted(por_tipo.items()))] if por_tipo else [],
    )


# --------------------------------------------------------------------------- nómina (SCRUM-65)
def _roles(f):
    from apps.nomina import calculo

    roles = calculo.nomina_del_mes(*f["mes"]).roles
    if f.get("equipo"):
        roles = [r for r in roles if r.esquema and r.esquema.pk == f["equipo"].pk]
    return calculo.ResumenNomina(roles)


def _nomina(f, usuario) -> Tabla:
    """Resumen de la nómina del mes por empleado."""
    from .formato import dinero

    nomina = _roles(f)
    filas = [[_nombre(r.empleado), str(r.empleado.cargo), str(r.esquema or ""), r.sueldo_del_mes, r.total_descuentos,
              r.total_anticipos, r.dias_trabajados, r.neto] for r in nomina.roles]
    return Tabla(
        columnas=[Columna("Empleado", peso=2.5), Columna("Cargo", peso=1.7), Columna("Equipo", peso=1.2), Columna("Sueldo del mes", "dinero"),
                  Columna("Descuentos", "dinero"), Columna("Anticipos", "dinero"), Columna("Días", "entero", .6),
                  Columna("Neto del rol", "dinero", 1.1)],
        filas=filas, periodo=_periodo_mes(f), horizontal=True,
        totales=[f"{len(filas)} empleados", "", "", nomina.total_sueldos, nomina.total_descuentos, nomina.total_anticipos, None,
                 nomina.total_neto],
        notas=[f"Comisiones del mes (se pagan aparte del rol): {dinero(nomina.total_comisiones)}. "
               f"Total a pagar: {dinero(nomina.total_a_pagar)}."],
    )


def _comisiones(f, usuario) -> Tabla:
    """Comisiones del mes de los vendedores."""
    nomina = _roles(f)
    filas = []
    for r in nomina.roles:
        if not r.es_vendedor:
            continue
        pct = r.porcentaje_comision if r.esquema and r.esquema.tipo != "MONTO_FIJO" else None
        filas.append([_nombre(r.empleado), str(r.esquema or "Administrativo"), r.total_ventas, pct, r.comision])
    return Tabla(
        columnas=[Columna("Vendedor", peso=2.6), Columna("Equipo", peso=1.4), Columna("Ventas cerradas", "dinero", 1.2),
                  Columna("Porcentaje", "porcentaje", .9), Columna("Comisión", "dinero", 1.1)],
        filas=filas, periodo=_periodo_mes(f),
        totales=[f"{len(filas)} vendedores", "", sum((x[2] for x in filas), Decimal("0")), None,
                 sum((x[4] for x in filas), Decimal("0"))],
    )


def _rol_pago(f, usuario) -> Tabla:
    """Rol de pago detallado de un empleado en el mes."""
    from apps.nomina import calculo

    e = calculo.estimar_mes(f["empleado"], *f["mes"])
    filas = [["Sueldo base mensual", e.sueldo_base or Decimal("0")]]
    if e.sueldo_proporcional:
        filas = [[f"Sueldo por {e.dias_contrato} de {calculo.DIAS_DEL_MES} días de contrato (base {e.sueldo_base})", e.sueldo_del_mes]]
    filas += [[f"{d.concepto} ({d.fecha:%d/%m/%Y})", -d.valor] for d in e.descuentos]
    if e.ajuste_tope:
        filas.append([f"Ajuste por tope de multas y faltas ({e.tope_porcentaje.normalize():f} % del sueldo)", e.ajuste_tope])
    filas += [["Anticipo" + (f": {a.concepto}" if a.concepto != "Anticipo" else ""), -a.valor] for a in e.anticipos]
    return Tabla(columnas=[Columna("Concepto", peso=4), Columna("Valor", "dinero")], filas=filas, periodo=_periodo_mes(f),
                 totales=["Neto a recibir", e.neto],
                 notas=[f"{_nombre(e.empleado)} · {e.dias_trabajados} días trabajados. La comisión va en su comprobante aparte."])


def _rol_pago_pdf(f) -> bytes:
    from apps.nomina import calculo

    return calculo.rol_de_pagos_pdf(calculo.estimar_mes(f["empleado"], *f["mes"]))


# --------------------------------------------------------------------------- comerciales (SCRUM-66)
def _produccion(f, usuario) -> Tabla:
    from apps.ventas.servicios import hoja_del_mes
    from apps.ventas.templatetags.humanize_cf import hora_aprobacion

    hoja = hoja_del_mes(f["vendedor"], *f["mes"])
    filas = []
    for i, r in enumerate(hoja["registros"], start=1):
        c = r.formulario.cliente
        filas.append([i, r.fecha, f"{c.nombres} {c.apellidos}".strip() or c.identificacion, hora_aprobacion(r.aprobacion),
                      c.ciudad, r.get_estado_display(), r.valor_venta])
    t = hoja["totales"]
    return Tabla(
        columnas=[Columna("N.°", "entero", .5), Columna("Fecha", "fecha", .9), Columna("Cliente", peso=2.6), Columna("Aprobación", peso=.9),
                  Columna("Ciudad", peso=1.2), Columna("Estado", peso=1.4), Columna("Valor", "dinero")],
        filas=filas, periodo=f"{hoja['asesor']} · {_periodo_mes(f)}",
        totales=["", "", "Total vendido", "", "", "", t["total_vendido"]],
        notas=[f"Enviados: {t['enviados']} · Cerradas: {t['cerradas']} · No concretadas: {t['no_concretadas']} · "
               f"Pendientes: {t['pendientes']}" + (f" · Tasa de cierre: {t['tasa_cierre']} %" if t["tasa_cierre"] is not None else "")],
    )


def ventas_por_vendedor(desde: date, hasta: date, vendedor=None, equipo=None) -> list[dict]:
    """Ventas por vendedor en el rango: enviados, cerradas, no concretadas, total, ticket y tasa de cierre."""
    from apps.ventas.models import EstadoProduccion as P, RegistroProduccion

    qs = RegistroProduccion.objects.filter(fecha__range=(desde, hasta))
    if vendedor:
        qs = qs.filter(hoja__vendedor=vendedor)
    equipos = equipos_al(hasta)
    filas = []
    for d in (qs.values("hoja__vendedor").annotate(
            enviados=Count("pk", filter=~Q(estado=P.ANULADO)), cerradas=Count("pk", filter=Q(estado=P.VENTA_CERRADA)),
            no_concretadas=Count("pk", filter=Q(estado=P.NO_CONCRETADA)), pendientes=Count("pk", filter=Q(estado=P.PENDIENTE)),
            total=Sum("valor_venta", filter=Q(estado=P.VENTA_CERRADA)))):
        esquema = equipos.get(d["hoja__vendedor"])
        if equipo and (not esquema or esquema.pk != equipo.pk):
            continue
        gestionadas = d["cerradas"] + d["no_concretadas"]
        total = d["total"] or Decimal("0")
        filas.append(d | {"vendedor_id": d["hoja__vendedor"], "esquema": esquema, "total": total,
                          "ticket": (total / d["cerradas"]) if d["cerradas"] else None,
                          "tasa": Decimal(d["cerradas"] * 100) / gestionadas if gestionadas else None})
    from apps.personal.models import Empleado

    nombres = {e.pk: e for e in Empleado.objects.filter(pk__in=[x["vendedor_id"] for x in filas])}
    for x in filas:
        x["vendedor"] = nombres[x["vendedor_id"]]
    return sorted(filas, key=lambda x: (-x["total"], -x["cerradas"], _nombre(x["vendedor"])))


def _ventas(f, usuario) -> Tabla:
    """Reporte de ventas por vendedor con los totales generales."""
    datos = ventas_por_vendedor(f["desde"], f["hasta"], f.get("vendedor"), f.get("equipo"))
    filas = [[_nombre(x["vendedor"]), str(x["esquema"] or "Administrativo"), x["enviados"], x["cerradas"], x["no_concretadas"],
              x["pendientes"], x["total"], x["ticket"], x["tasa"]] for x in datos]
    tot = {k: sum(x[k] for x in datos) for k in ("enviados", "cerradas", "no_concretadas", "pendientes")}
    total = sum((x["total"] for x in datos), Decimal("0"))
    gestionadas = tot["cerradas"] + tot["no_concretadas"]
    return Tabla(
        columnas=[Columna("Vendedor", peso=2.4), Columna("Equipo", peso=1.3), Columna("Enviados", "entero", .8), Columna("Cerradas", "entero", .8),
                  Columna("No concretadas", "entero", .9), Columna("Pendientes", "entero", .8), Columna("Total vendido", "dinero", 1.2),
                  Columna("Ticket promedio", "dinero", 1.1), Columna("Tasa de cierre", "porcentaje", .9)],
        filas=filas, periodo=_periodo_rango(f), horizontal=True,
        totales=["Totales", "", tot["enviados"], tot["cerradas"], tot["no_concretadas"], tot["pendientes"], total,
                 total / tot["cerradas"] if tot["cerradas"] else None,
                 Decimal(tot["cerradas"] * 100) / gestionadas if gestionadas else None],
        notas=["Cada DAMI cuenta en la fecha en que se envió. Tasa de cierre = cerradas ÷ (cerradas + no concretadas)."],
    )


def _formularios(f, usuario) -> Tabla:
    """Formularios DAMI enviados en el rango, con filtros por estado y vendedor."""
    from apps.ventas.models import FormularioDAMI

    qs = (FormularioDAMI.objects.exclude(estado=FormularioDAMI.Estado.BORRADOR)
          .filter(enviado_en__date__range=(f["desde"], f["hasta"]))
          .select_related("cliente", "vendedor", "encargado_asignado", "registro_produccion").order_by("enviado_en", "pk"))
    if f.get("estado"):
        qs = qs.filter(estado=f["estado"])
    if f.get("vendedor"):
        qs = qs.filter(vendedor=f["vendedor"])
    filas = []
    for d in qs:
        c, registro = d.cliente, getattr(d, "registro_produccion", None)
        filas.append([str(d), d.enviado_en, _nombre(d.vendedor), f"{c.nombres} {c.apellidos}".strip() or c.identificacion, c.ciudad,
                      d.get_estado_display(), _nombre(d.encargado_asignado) if d.encargado_asignado else "",
                      registro.valor_venta if registro else None])
    return Tabla(
        columnas=[Columna("DAMI", peso=1.1), Columna("Enviado", "fecha", 1.3), Columna("Vendedor", peso=1.9), Columna("Cliente", peso=2),
                  Columna("Ciudad", peso=1.1), Columna("Estado", peso=1.3), Columna("Supervisor", peso=1.7), Columna("Valor", "dinero")],
        filas=filas, periodo=_periodo_rango(f), horizontal=True,
        totales=[f"{len(filas)} formularios", "", "", "", "", "", "Total vendido", sum((x[7] or Decimal("0") for x in filas), Decimal("0"))],
        notas=["Sin datos de la tarjeta de pago."],
    )


def _metas(f, usuario) -> Tabla:
    """Avance de cada vendedor frente a su meta del mes."""
    from apps.cierre.servicios import panel_comercial

    panel = panel_comercial(*f["mes"], usuario)
    filas = [fila for fila in panel.filas if not f.get("vendedor") or fila.vendedor.pk == f["vendedor"].pk]
    t = panel.totales
    return Tabla(
        columnas=[Columna("Posición", "entero", .7), Columna("Vendedor", peso=2.4), Columna("Formularios", "entero", .9),
                  Columna("Cerradas", "entero", .8), Columna("Vendido", "dinero", 1.1), Columna("Meta", "dinero", 1.1),
                  Columna("Avance", "porcentaje", .8), Columna("Falta para la meta", "dinero", 1.2)],
        filas=[[x.posicion, _nombre(x.vendedor), x.total_formularios, x.ventas_cerradas, x.total_vendido, x.monto_meta,
                x.porcentaje_meta, x.pendiente_meta] for x in filas],
        periodo=_periodo_mes(f), horizontal=True,
        totales=None if f.get("vendedor") else ["", "Totales", t["total_formularios"], t["ventas_cerradas"], t["total_vendido"],
                                                t["monto_meta"], t["porcentaje_meta"], t["pendiente_meta"]],
        notas=[f"{t['cumplieron']} vendedor(es) cumplieron su meta."] if not f.get("vendedor") else [],
    )


# --------------------------------------------------------------------------- bases comerciales (SCRUM-97)
def _filtros_bases(f) -> dict:
    """Filtros de los reportes de bases para los servicios de ventas."""
    desde, hasta = f.get("llamado_desde"), f.get("llamado_hasta")
    gestion = (desde or date(2000, 1, 1), hasta or timezone.localdate()) if desde or hasta else None
    return {"base": f.get("base"), "vendedor": f.get("vendedor"), "ciudad": f.get("ciudad"), "tarjeta": f.get("tarjeta"),
            "gestion": gestion}


def _periodo_bases(f) -> str:
    """Texto del periodo de llamadas para el encabezado de los reportes de bases."""
    desde, hasta = f.get("llamado_desde"), f.get("llamado_hasta")
    if desde and hasta:
        return f"Llamadas del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"
    if desde:
        return f"Llamadas desde el {desde:%d/%m/%Y}"
    if hasta:
        return f"Llamadas hasta el {hasta:%d/%m/%Y}"
    return f"Al {timezone.localdate():%d/%m/%Y}"


SUMABLES_BASES = ("contactos", "enviados", "gestionados", "contestaron", "no_contesto", "no_quiere", "no_entregada", "dami_sacado")


def _efectividad_bases(f, usuario) -> Tabla:
    """Efectividad de cada base comercial: contactos gestionados y DAMI conseguidos."""
    from apps.ventas.servicios import _completar, efectividad_bases, efectividad_por

    filtros = _filtros_bases(f)
    datos = efectividad_bases(**filtros)
    filas = [[x["base"].nombre, timezone.localtime(x["base"].creado_en).date(), x["contactos"], x["gestionados"], x["contestaron"], x["no_contesto"],
              x["conexion"], x["no_quiere"], x["no_entregada"], x["dami_sacado"], x["efectividad"], x["efectividad_contestaron"]]
             for x in datos]
    con_gestion = [x["efectividad"] for x in datos if x["efectividad"] is not None]
    media = sum(con_gestion, Decimal("0")) / len(con_gestion) if con_gestion else None
    suma = {k: sum(x[k] for x in datos) for k in SUMABLES_BASES}
    total = _completar({**suma, "pendientes": 0})
    from .formato import porcentaje

    return Tabla(
        columnas=[Columna("Base", peso=1.55), Columna("Cargada", "fecha", .95), Columna("Contactos", "entero", 1.05),
                  Columna("Llamados", "entero", 1), Columna("Contestó", "entero", .95), Columna("No contestó", "entero", 1.1),
                  Columna("Conexión", "porcentaje", 1), Columna("No quiere", "entero", 1), Columna("No entregada", "entero", 1.3),
                  Columna("DAMI efect.", "entero", 1.05), Columna("Efectividad", "porcentaje", 1.1),
                  Columna("Ef. contestó", "porcentaje", 1.15)],
        filas=filas, periodo=_periodo_bases(f), horizontal=True,
        totales=["Totales", None, suma["contactos"], suma["gestionados"], suma["contestaron"], suma["no_contesto"], total["conexion"],
                 suma["no_quiere"], suma["no_entregada"], suma["dami_sacado"], total["efectividad"], total["efectividad_contestaron"]],
        notas=["Llamados = contactos ya categorizados. Contestó = no quiere + tarjeta no entregada + DAMI efectivo; "
               "conexión = contestó ÷ llamados. No entregada = tarjeta no entregada.",
               "Efectividad = DAMI efectivos ÷ llamados; ef. contestó = DAMI efectivos ÷ los que contestaron."]
        + ([f"Media de efectividad de las bases: {porcentaje(media)} ({len(con_gestion)} bases con gestión)."] if media is not None else []),
        vacio="No hay bases con contactos para los filtros elegidos.",
        vista="reportes/_efectividad_bases.html",
        extra=_vista_efectividad(datos, total, media, {
            "asesor": efectividad_por("vendedor", **filtros), "ciudad": efectividad_por("ciudad", **filtros),
            "tarjeta": efectividad_por("categoria_tarjeta", **filtros)}),
    )


def _partes_conexion(x) -> list:
    """Cómo terminaron las llamadas, sobre los contactos (incluye lo que falta llamar)."""
    falta = max(x["contactos"] - x["gestionados"], 0) if "contactos" in x else x.get("pendientes", 0)
    total = (x["gestionados"] + falta) or 1
    return [{"clase": c, "nombre": n, "valor": v, "ancho": round(v * 100 / total, 2)} for c, n, v in (
        ("dami", "DAMI efectivo", x["dami_sacado"]), ("no-entregada", "Tarjeta no entregada", x["no_entregada"]),
        ("no-quiere", "No quiere", x["no_quiere"]), ("no-contesto", "No contestó", x["no_contesto"]),
        ("pendiente", "Falta llamar", falta)) if v]


def _vista_efectividad(datos, total, media, desgloses) -> dict:
    """Datos de la vista visual: DAMI efectivos, conexión con los clientes, cada base y los desgloses."""
    bases = []
    for x in datos:
        e = x["efectividad"]
        bases.append({
            "nombre": x["base"].nombre, "cargada": x["base"].creado_en, "contactos": x["contactos"], "gestionados": x["gestionados"],
            "dami": x["dami_sacado"], "efectividad": e, "falta": max(x["contactos"] - x["gestionados"], 0),
            "contestaron": x["contestaron"], "no_contesto": x["no_contesto"], "conexion": x["conexion"],
            "no_entregada": x["no_entregada"], "efectividad_contestaron": x["efectividad_contestaron"],
            "avance": round(x["gestionados"] * 100 / x["contactos"]) if x["contactos"] else 0, "partes": _partes_conexion(x),
            "nivel": None if e is None or media is None or len(datos) < 2 else
            ("igual" if round(e) == round(media) else "alta" if e > media else "baja"),
        })
    bases.sort(key=lambda b: (b["efectividad"] is None, -(b["efectividad"] or 0)))
    for filas in desgloses.values():
        for d in filas:
            d["partes"] = _partes_conexion(d)
    general = total["efectividad"]
    contestaron_ancho = round(total["contestaron"] * 100 / total["gestionados"], 2) if total["gestionados"] else 0
    return {
        "bases": bases, "media": media, "general": general, "t": total,
        "contactos": total["contactos"], "gestionados": total["gestionados"], "dami": total["dami_sacado"],
        "avance": round(total["gestionados"] * 100 / total["contactos"]) if total["contactos"] else 0,
        "falta": max(total["contactos"] - total["gestionados"], 0),
        "de_cada_100": round(general) if general is not None else None,
        "conexion_100": round(total["conexion"]) if total["conexion"] is not None else None,
        "contestaron_ancho": contestaron_ancho, "no_contesto_ancho": round(100 - contestaron_ancho, 2) if total["gestionados"] else 0,
        "desgloses": [("asesor", "Por asesor", "Asesor", desgloses["asesor"]), ("ciudad", "Por ciudad", "Ciudad", desgloses["ciudad"]),
                      ("tarjeta", "Por categoría de tarjeta", "Categoría", desgloses["tarjeta"])],
    }


def _gestion_bases(f, usuario) -> Tabla:
    """Gestión de bases por vendedor: enviados, gestionados, resultados y avance."""
    from apps.ventas.servicios import seguimiento

    filtros = _filtros_bases(f)
    datos = seguimiento(**filtros)
    filas = [[_nombre(x["vendedor"]), x["base"].nombre, x["enviados"], x["gestionados"], x["pendientes"], x["contestaron"],
              x["no_contesto"], x["no_quiere"], x["no_entregada"], x["dami_sacado"], x["efectividad"], x["avance"]] for x in datos]
    suma = {k: sum(x[k] for x in datos) for k in ("enviados", "gestionados", "pendientes", "contestaron", "no_contesto", "no_quiere",
                                                  "no_entregada", "dami_sacado")}
    return Tabla(
        columnas=[Columna("Vendedor", peso=1.8), Columna("Base", peso=1.45), Columna("Enviados", "entero", 1), Columna("Llamados", "entero", 1),
                  Columna("Faltan", "entero", .8), Columna("Contestó", "entero", .95), Columna("No contestó", "entero", 1.1),
                  Columna("No quiere", "entero", 1), Columna("No entregada", "entero", 1.3), Columna("DAMI efect.", "entero", 1.05),
                  Columna("Efectividad", "porcentaje", 1.1), Columna("Avance", "porcentaje", .9)],
        filas=filas, periodo=_periodo_bases(f), horizontal=True,
        totales=["Totales", "", suma["enviados"], suma["gestionados"], suma["pendientes"], suma["contestaron"], suma["no_contesto"],
                 suma["no_quiere"], suma["no_entregada"], suma["dami_sacado"],
                 Decimal(suma["dami_sacado"] * 100) / suma["gestionados"] if suma["gestionados"] else None,
                 Decimal(suma["gestionados"] * 100) / suma["enviados"] if suma["enviados"] else None],
        notas=["«Faltan» son los contactos que el vendedor recibió y todavía no categorizó. No entregada = tarjeta no entregada. "
               "Efectividad = DAMI efectivos ÷ llamados."],
        vacio="Todavía no se enviaron contactos a vendedores.",
    )


# --------------------------------------------------------------------------- TI
def _ti_equipos(f, usuario) -> Tabla:
    from apps.ti.models import Equipo

    qs = Equipo.objects.select_related("asignado_a").order_by("tipo", "codigo")
    if f.get("tipo_equipo"):
        qs = qs.filter(tipo=f["tipo_equipo"])
    if f.get("estado"):
        qs = qs.filter(estado=f["estado"])
    hoy = timezone.localdate()
    filas, total = [], Decimal("0")
    for e in qs:
        garantia = (f"{e.garantia_hasta:%d/%m/%Y}" + (" (vencida)" if e.garantia_hasta < hoy else "")) if e.garantia_hasta else ""
        filas.append([e.codigo, e.get_tipo_display(), " ".join(x for x in (e.marca, e.modelo) if x), e.numero_serie,
                      e.direccion_ip or "", _nombre(e.asignado_a) if e.asignado_a else "", e.ubicacion, e.get_estado_display(),
                      garantia, e.costo])
        total += e.costo or 0
    por_estado = defaultdict(int)
    for fila in filas:
        por_estado[fila[7]] += 1
    return Tabla(
        columnas=[Columna("Código", peso=.9), Columna("Tipo", peso=1.4), Columna("Marca y modelo", peso=1.7), Columna("Serie", peso=1.2),
                  Columna("IP", peso=1.1), Columna("Asignado a", peso=1.6), Columna("Ubicación", peso=1.2), Columna("Estado", peso=1),
                  Columna("Garantía", peso=1.2), Columna("Costo", "dinero", .9)],
        filas=filas, periodo=f"Al {hoy:%d/%m/%Y}", horizontal=True,
        totales=["Totales", f"{len(filas)} equipos", "", "", "", "", "", "", "", total] if filas else None,
        notas=[" · ".join(f"{estado}: {n}" for estado, n in sorted(por_estado.items()))] if por_estado else [],
        vacio="No hay equipos registrados con esos filtros.",
    )


def _ti_eventos(f, usuario) -> Tabla:
    """Eventos de la hoja de vida de los equipos en el rango, con el costo total."""
    from apps.ti.models import EventoEquipo

    qs = (EventoEquipo.objects.filter(fecha__range=(f["desde"], f["hasta"])).select_related("equipo", "empleado", "registrado_por")
          .order_by("fecha", "pk"))
    if f.get("tipo_equipo"):
        qs = qs.filter(equipo__tipo=f["tipo_equipo"])
    filas = [[ev.fecha, ev.equipo.codigo, ev.equipo.get_tipo_display(), ev.get_tipo_display(), ev.descripcion, ev.realizado_por,
              ev.costo] for ev in qs]
    total = sum((fila[6] or 0 for fila in filas), Decimal("0"))
    return Tabla(
        columnas=[Columna("Fecha", "fecha", .9), Columna("Equipo", peso=.8), Columna("Tipo de equipo", peso=1.3),
                  Columna("Evento", peso=1.3), Columna("Descripción", peso=3.2), Columna("Realizado por", peso=1.3),
                  Columna("Costo", "dinero", .9)],
        filas=filas, periodo=_periodo_rango(f), horizontal=True,
        totales=["Totales", "", "", f"{len(filas)} eventos", "", "", total] if filas else None,
        vacio="No hay mantenimientos ni movimientos de equipos en esas fechas.",
    )


def _ti_conexiones(f, usuario) -> Tabla:
    """Conexiones a CALLFLOW por usuario: sesiones, IP usadas y sesiones cerradas por TI."""
    from apps.ti.servicios import conexiones_por_usuario

    datos = conexiones_por_usuario(f["desde"], f["hasta"])
    if f.get("usuario"):
        datos = datos.filter(usuario=f["usuario"])
    roles = dict(Rol.choices)
    filas = [[f"{x['usuario__first_name']} {x['usuario__last_name']}".strip() or x["usuario__username"], x["usuario__username"],
              roles.get(x["usuario__rol"], x["usuario__rol"]), x["sesiones"], x["ips"], x["cerradas_ti"], x["ultima"]] for x in datos]
    return Tabla(
        columnas=[Columna("Persona", peso=2), Columna("Usuario", peso=1.5), Columna("Rol", peso=1.4), Columna("Sesiones", "entero", .9),
                  Columna("IP distintas", "entero", .9), Columna("Cerradas por TI", "entero", 1), Columna("Última actividad", "fecha", 1.4)],
        filas=filas, periodo=_periodo_rango(f), horizontal=True,
        totales=["Totales", "", "", sum(x[3] for x in filas), "", sum(x[5] for x in filas), None] if filas else None,
        notas=["«IP distintas» mayor a 1 indica que la persona entró desde varios equipos en el periodo."],
        vacio="No hay conexiones en esas fechas.",
    )


def _ti_errores(f, usuario) -> Tabla:
    """Errores del sistema en el periodo (logs/errores.jsonl), los iguales agrupados, más el resumen de seguridad."""
    from datetime import datetime, time, timedelta

    from apps.ti import registro_errores

    zona = timezone.get_current_timezone()
    desde = datetime.combine(f["desde"], time.min, tzinfo=zona)
    hasta = datetime.combine(f["hasta"] + timedelta(days=1), time.min, tzinfo=zona)
    errores = registro_errores.errores_entre(desde, hasta)
    grupos = registro_errores.agrupar(errores)
    filas = []
    for g in grupos:
        if str(g.get("origen", "")).startswith("respaldo."):
            tipo, donde = "Respaldo fallido", g["origen"].split(".", 1)[1].capitalize()
        else:
            tipo = (f"HTTP {g['estado_http']} · " if g.get("estado_http") else "") + (g.get("excepcion") or g.get("nivel", "ERROR"))
            donde = f"{g.get('metodo', '')} {g['ruta']}".strip() if g.get("ruta") else g.get("origen", "")
        # La causa real está en la última línea de la traza («ValueError: …»); el mensaje suele ser «Internal Server Error».
        causa = (g.get("traza") or "").strip().splitlines()[-1:] or [str(g.get("mensaje", ""))]
        filas.append([tipo, donde, causa[0][:220], g["veces"], g["primera"], g["ultima"],
                      ", ".join(sorted(g["usuarios"]))[:120]])

    # Errores por día: para la vista en pantalla y una nota con el día con más errores.
    por_dia: dict = {}
    for e in errores:
        dia = timezone.localtime(e["fecha"]).date()
        por_dia[dia] = por_dia.get(dia, 0) + 1
    dias, d = [], f["desde"]
    while d <= f["hasta"]:
        dias.append({"etiqueta": f"{('lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom')[d.weekday()]} {d:%d/%m}",
                     "valor": por_dia.get(d, 0)})
        d += timedelta(days=1)
    maximo = max((x["valor"] for x in dias), default=0) or 1
    for x in dias:
        x["ancho"] = round(x["valor"] * 100 / maximo)

    seguridad = registro_errores.seguridad_entre(desde, hasta)
    conteo = {clave: sum(1 for e in seguridad if e.get("evento") == clave)
              for clave in ("ingreso_fallido", "ingreso_bloqueado", "limite_peticiones")}
    respaldos_fallidos = sum(g["veces"] for g in grupos if str(g.get("origen", "")).startswith("respaldo."))
    notas = [f"{len(errores)} error{'es' if len(errores) != 1 else ''} en {len(grupos)} tipo{'s' if len(grupos) != 1 else ''} "
             f"distinto{'s' if len(grupos) != 1 else ''}. Los errores iguales se agrupan (misma causa y misma página)."]
    if errores:
        peor = max(por_dia.items(), key=lambda x: x[1])
        notas.append(f"Día con más errores: {peor[0]:%d/%m/%Y} ({peor[1]}).")
    notas.append(f"Seguridad en el periodo: {conteo['ingreso_fallido']} ingresos fallidos, {conteo['ingreso_bloqueado']} cuentas "
                 f"bloqueadas y {conteo['limite_peticiones']} rechazos por exceso de peticiones (detalle en TI › Accesos).")
    notas.append("La traza completa de cada error está en TI › Monitoreo › Servidor.")
    return Tabla(
        columnas=[Columna("Tipo", peso=1.5), Columna("Dónde", peso=1.6), Columna("Mensaje", peso=3.2), Columna("Veces", "entero", .7),
                  Columna("Primera vez", "fecha", 1.2), Columna("Última vez", "fecha", 1.2), Columna("Usuarios", peso=1.3)],
        filas=filas, periodo=_periodo_rango(f), horizontal=True,
        totales=["Totales", f"{len(grupos)} tipos", "", len(errores), None, None, ""] if filas else None,
        notas=notas, vacio="No se registraron errores en el periodo.", vista="reportes/_errores_ti.html",
        extra={"total": len(errores), "tipos": len(grupos), "dias": dias, "respaldos": respaldos_fallidos,
               "seguridad": conteo, "dias_con_error": sum(1 for x in dias if x["valor"])},
    )


_CONSTRUCTORES = {
    "BASES_EFECTIVIDAD": _efectividad_bases, "BASES_GESTION": _gestion_bases,
    "PERSONAL_LISTADO": _personal, "ASISTENCIA_HISTORIAL": _asistencia, "INVENTARIO_STOCK": _inventario,
    "INVENTARIO_MOVIMIENTOS": _movimientos, "NOMINA_RESUMEN": _nomina, "NOMINA_COMISIONES": _comisiones,
    "NOMINA_ROL_PAGO": _rol_pago, "VENTAS_PRODUCCION": _produccion, "VENTAS_POR_VENDEDOR": _ventas,
    "VENTAS_FORMULARIOS": _formularios, "CIERRE_PANEL": _metas,
    "TI_EQUIPOS": _ti_equipos, "TI_EVENTOS": _ti_eventos, "TI_CONEXIONES": _ti_conexiones, "TI_ERRORES": _ti_errores,
}
_DOCUMENTOS = {"NOMINA_ROL_PAGO": _rol_pago_pdf}  # tienen su propio formato de PDF

_ESTADOS_EMPLEADO = (("ACTIVO", "Activo"), ("VACACIONES", "Vacaciones"), ("LICENCIA", "Licencia"), ("INACTIVO", "Inactivo"),
                     ("DESVINCULADO", "Desvinculado"))
_ESTADOS_DAMI = (("ENVIADO", "Enviado a cierre"), ("EN_GESTION", "En gestión"), ("VENTA_CERRADA", "Venta cerrada"),
                 ("NO_CONCRETADA", "Venta no concretada"), ("ANULADO", "Anulado"))

REPORTES = {r.codigo: r for r in [
    Reporte("PERSONAL_LISTADO", "Listado del personal", "ADMINISTRATIVO",
            "Empleados con cargo, contrato, fecha de ingreso, sueldo vigente y estado.", ("estado", "cargo"), (A,),
            estados=_ESTADOS_EMPLEADO, icono="people"),
    Reporte("ASISTENCIA_HISTORIAL", "Resumen de asistencia", "ADMINISTRATIVO",
            "Días con marcación, faltas, atrasos y minutos de atraso de cada empleado en un rango de fechas.",
            ("desde", "hasta", "empleado"), (A,), icono="clock-history"),
    Reporte("INVENTARIO_STOCK", "Existencias de inventario", "ADMINISTRATIVO",
            "Stock actual y mínimo de cada material, con los que están por debajo del mínimo.", ("categoria", "estado"), (A,),
            estados=(("BAJO", "Solo stock bajo"),), icono="box-seam"),
    Reporte("INVENTARIO_MOVIMIENTOS", "Movimientos de inventario", "ADMINISTRATIVO",
            "Entradas, salidas y ajustes de materiales en un rango de fechas.", ("desde", "hasta", "categoria"), (A,),
            icono="arrow-left-right"),
    Reporte("NOMINA_RESUMEN", "Resumen de nómina", "NOMINA",
            "Sueldo, descuentos, anticipos y neto del rol de cada empleado en un mes.", ("mes", "equipo"), (A, K), icono="cash-coin"),
    Reporte("NOMINA_COMISIONES", "Comisiones de vendedores", "NOMINA",
            "Ventas cerradas, porcentaje aplicado y comisión de cada vendedor en un mes.", ("mes", "equipo"), (A, K), icono="percent"),
    Reporte("NOMINA_ROL_PAGO", "Rol de pagos individual", "NOMINA",
            "El rol de pagos de un empleado con el formato de la empresa.", ("mes", "empleado"), (A, K), obligatorios=("empleado",),
            icono="receipt"),
    Reporte("VENTAS_POR_VENDEDOR", "Ventas por vendedor", "VENTAS",
            "Enviados, cerradas, total vendido, ticket promedio y tasa de cierre de cada vendedor.",
            ("desde", "hasta", "equipo", "vendedor"), (A, C), icono="graph-up"),
    Reporte("VENTAS_FORMULARIOS", "Formularios DAMI", "VENTAS",
            "Detalle de los DAMI enviados: asesor, cliente, estado, supervisor y valor.", ("desde", "hasta", "estado", "vendedor"),
            (A, C), estados=_ESTADOS_DAMI, icono="file-earmark-text"),
    Reporte("VENTAS_PRODUCCION", "Hoja de producción del vendedor", "VENTAS",
            "La hoja de producción de un vendedor en un mes, con sus totales.", ("mes", "vendedor"), (A, C),
            obligatorios=("vendedor",), icono="table"),
    Reporte("BASES_EFECTIVIDAD", "Efectividad de bases comerciales", "VENTAS",
            "Por base: DAMI efectivos, conexión con los clientes (contestaron o no), tarjetas no entregadas y efectividad, "
            "con desglose por asesor, ciudad y categoría de tarjeta.",
            ("base", "vendedor", "ciudad", "tarjeta", "llamado_desde", "llamado_hasta"), (A, C), icono="database-check"),
    Reporte("BASES_GESTION", "Gestión de bases por vendedor", "VENTAS",
            "Contactos enviados a cada vendedor, cuántos gestionó, cuántos le faltan y cómo los categorizó.",
            ("base", "vendedor", "ciudad", "tarjeta", "llamado_desde", "llamado_hasta"), (A, C), icono="person-lines-fill"),
    Reporte("CIERRE_PANEL", "Cumplimiento de metas", "CIERRE",
            "Meta, vendido, porcentaje de avance y lo que falta de cada vendedor en un mes.", ("mes", "vendedor"), (A, C),
            icono="bullseye"),
    Reporte("TI_EQUIPOS", "Inventario de equipos", "TI",
            "Todos los equipos físicos con serie, IP, a quién están asignados, ubicación, estado, garantía y costo.",
            ("tipo_equipo", "estado"), (T,), estados=(("OPERATIVO", "Operativo"), ("BODEGA", "En bodega"),
                                                       ("REPARACION", "En reparación"), ("BAJA", "Dado de baja")), icono="pc-display"),
    Reporte("TI_EVENTOS", "Mantenimientos y movimientos de equipos", "TI",
            "Altas, asignaciones, devoluciones, mantenimientos, reparaciones y bajas en un rango de fechas, con su costo.",
            ("desde", "hasta", "tipo_equipo"), (T,), icono="tools"),
    Reporte("TI_CONEXIONES", "Conexiones al sistema por usuario", "TI",
            "Cuántas veces entró cada persona a CALLFLOW, desde cuántos equipos y su última actividad.",
            ("desde", "hasta", "usuario"), (T,), icono="wifi"),
    Reporte("TI_ERRORES", "Errores del sistema", "TI",
            "Todos los errores del sistema de los últimos 7 días, del mes o de las fechas que elija: cuántas veces pasó "
            "cada uno, dónde, a quién y cuándo, más respaldos fallidos e intentos de ingreso.",
            ("rango", "desde", "hasta"), (T,), icono="bug"),
]}

# El panel de estadísticas se registra como tipo de reporte para usar los mismos permisos por rol.
ESTADISTICAS = Reporte("VENTAS_ESTADISTICAS", "Estadísticas y proyección de ventas", "VENTAS",
                       "Gráficas de ventas por mes, ranking, equipos y proyecciones del mes y de los próximos meses.",
                       ("mes", "equipo", "vendedor"), (A,), icono="bar-chart-line")

# Para inicializar_datos: (código, nombre, módulo, filtros, roles, descripción)
TIPOS_REPORTE = [(r.codigo, r.nombre, r.modulo, list(r.filtros), list(r.roles), r.descripcion)
                 for r in [*REPORTES.values(), ESTADISTICAS]]
