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

A, V, C, T = Rol.ADMINISTRADOR, Rol.VENDEDOR, Rol.ENCARGADO_CIERRE, Rol.TI


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
    from .formato import dinero

    nomina = _roles(f)
    filas = [[_nombre(r.empleado), str(r.empleado.cargo), str(r.esquema or ""), r.sueldo_base, r.total_descuentos,
              r.total_anticipos, r.dias_trabajados, r.neto] for r in nomina.roles]
    return Tabla(
        columnas=[Columna("Empleado", peso=2.5), Columna("Cargo", peso=1.7), Columna("Equipo", peso=1.2), Columna("Sueldo base", "dinero"),
                  Columna("Descuentos", "dinero"), Columna("Anticipos", "dinero"), Columna("Días", "entero", .6),
                  Columna("Neto del rol", "dinero", 1.1)],
        filas=filas, periodo=_periodo_mes(f), horizontal=True,
        totales=[f"{len(filas)} empleados", "", "", nomina.total_sueldos, nomina.total_descuentos, nomina.total_anticipos, None,
                 nomina.total_neto],
        notas=[f"Comisiones del mes (se pagan aparte del rol): {dinero(nomina.total_comisiones)}. "
               f"Total a pagar: {dinero(nomina.total_a_pagar)}."],
    )


def _comisiones(f, usuario) -> Tabla:
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
    from apps.nomina import calculo

    e = calculo.estimar_mes(f["empleado"], *f["mes"])
    filas = [["Sueldo base", e.sueldo_base or Decimal("0")]]
    filas += [[f"{d.concepto} ({d.fecha:%d/%m/%Y})", -d.valor] for d in e.descuentos]
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
def _efectividad_bases(f, usuario) -> Tabla:
    from apps.ventas.servicios import efectividad_bases

    datos = efectividad_bases(f.get("base"))
    filas = [[x["base"].nombre, x["base"].creado_en, x["contactos"], x["enviados"], x["gestionados"], x["no_contesto"],
              x["no_quiere"], x["dami_sacado"], x["efectividad"], x["efectividad_total"]] for x in datos]
    con_gestion = [x["efectividad"] for x in datos if x["efectividad"] is not None]
    media = sum(con_gestion, Decimal("0")) / len(con_gestion) if con_gestion else None
    suma = {k: sum(x[k] for x in datos) for k in ("contactos", "enviados", "gestionados", "no_contesto", "no_quiere", "dami_sacado")}
    from .formato import porcentaje

    return Tabla(
        columnas=[Columna("Base", peso=2.3), Columna("Cargada", "fecha"), Columna("Contactos", "entero", .9), Columna("Enviados", "entero", .9),
                  Columna("Gestionados", "entero"), Columna("No contestó", "entero"), Columna("No quiere", "entero", .9),
                  Columna("DAMI sacado", "entero"), Columna("Efectividad", "porcentaje", .9), Columna("% del total", "porcentaje", .9)],
        filas=filas, periodo=f"Al {timezone.localdate():%d/%m/%Y}", horizontal=True,
        totales=["Totales", None, suma["contactos"], suma["enviados"], suma["gestionados"], suma["no_contesto"], suma["no_quiere"],
                 suma["dami_sacado"], Decimal(suma["dami_sacado"] * 100) / suma["gestionados"] if suma["gestionados"] else None,
                 Decimal(suma["dami_sacado"] * 100) / suma["contactos"] if suma["contactos"] else None],
        notas=["Efectividad = DAMI sacados ÷ contactos gestionados; % del total = DAMI sacados ÷ contactos de la base."]
        + ([f"Media de efectividad de las bases: {porcentaje(media)} ({len(con_gestion)} bases con gestión)."] if media is not None else []),
        vacio="No hay bases cargadas.",
        vista="reportes/_efectividad_bases.html", extra=_vista_efectividad(datos, suma, media),
    )


def _vista_efectividad(datos, suma, media) -> dict:
    """Datos de la vista visual: resumen general y, por base, la barra de cómo terminaron sus contactos."""

    def partes(x):
        falta = max(x["contactos"] - x["gestionados"], 0)
        total = x["contactos"] or 1
        return [{"clase": c, "nombre": n, "valor": v, "ancho": round(v * 100 / total, 2)} for c, n, v in (
            ("dami", "DAMI sacado", x["dami_sacado"]), ("no-quiere", "No quiere", x["no_quiere"]),
            ("no-contesto", "No contestó", x["no_contesto"]), ("pendiente", "Falta llamar", falta)) if v]

    bases = []
    for x in datos:
        e = x["efectividad"]
        bases.append({
            "nombre": x["base"].nombre, "cargada": x["base"].creado_en, "contactos": x["contactos"], "gestionados": x["gestionados"],
            "dami": x["dami_sacado"], "efectividad": e, "falta": max(x["contactos"] - x["gestionados"], 0),
            "avance": round(x["gestionados"] * 100 / x["contactos"]) if x["contactos"] else 0, "partes": partes(x),
            "nivel": None if e is None or media is None or len(datos) < 2 else
            ("igual" if round(e) == round(media) else "alta" if e > media else "baja"),
        })
    bases.sort(key=lambda b: (b["efectividad"] is None, -(b["efectividad"] or 0)))
    general = Decimal(suma["dami_sacado"] * 100) / suma["gestionados"] if suma["gestionados"] else None
    return {
        "bases": bases, "media": media, "general": general, "contactos": suma["contactos"], "gestionados": suma["gestionados"],
        "dami": suma["dami_sacado"], "avance": round(suma["gestionados"] * 100 / suma["contactos"]) if suma["contactos"] else 0,
        "de_cada_100": round(general) if general is not None else None,
    }


def _gestion_bases(f, usuario) -> Tabla:
    from apps.ventas.servicios import seguimiento

    datos = seguimiento(base=f.get("base"), vendedor=f.get("vendedor"))
    filas = [[_nombre(x["vendedor"]), x["base"].nombre, x["enviados"], x["gestionados"], x["pendientes"], x["no_contesto"],
              x["no_quiere"], x["dami_sacado"], x["avance"]] for x in datos]
    suma = {k: sum(x[k] for x in datos) for k in ("enviados", "gestionados", "pendientes", "no_contesto", "no_quiere", "dami_sacado")}
    return Tabla(
        columnas=[Columna("Vendedor", peso=2.2), Columna("Base", peso=1.8), Columna("Enviados", "entero", .9), Columna("Gestionados", "entero"),
                  Columna("Faltan", "entero", .8), Columna("No contestó", "entero"), Columna("No quiere", "entero", .9),
                  Columna("DAMI sacado", "entero"), Columna("Avance", "porcentaje", .8)],
        filas=filas, periodo=f"Al {timezone.localdate():%d/%m/%Y}", horizontal=True,
        totales=["Totales", "", suma["enviados"], suma["gestionados"], suma["pendientes"], suma["no_contesto"], suma["no_quiere"],
                 suma["dami_sacado"], Decimal(suma["gestionados"] * 100) / suma["enviados"] if suma["enviados"] else None],
        notas=["«Faltan» son los contactos que el vendedor recibió y todavía no categorizó."],
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


_CONSTRUCTORES = {
    "BASES_EFECTIVIDAD": _efectividad_bases, "BASES_GESTION": _gestion_bases,
    "PERSONAL_LISTADO": _personal, "ASISTENCIA_HISTORIAL": _asistencia, "INVENTARIO_STOCK": _inventario,
    "INVENTARIO_MOVIMIENTOS": _movimientos, "NOMINA_RESUMEN": _nomina, "NOMINA_COMISIONES": _comisiones,
    "NOMINA_ROL_PAGO": _rol_pago, "VENTAS_PRODUCCION": _produccion, "VENTAS_POR_VENDEDOR": _ventas,
    "VENTAS_FORMULARIOS": _formularios, "CIERRE_PANEL": _metas,
    "TI_EQUIPOS": _ti_equipos, "TI_EVENTOS": _ti_eventos, "TI_CONEXIONES": _ti_conexiones,
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
            "Sueldo, descuentos, anticipos y neto del rol de cada empleado en un mes.", ("mes", "equipo"), (A,), icono="cash-coin"),
    Reporte("NOMINA_COMISIONES", "Comisiones de vendedores", "NOMINA",
            "Ventas cerradas, porcentaje aplicado y comisión de cada vendedor en un mes.", ("mes", "equipo"), (A,), icono="percent"),
    Reporte("NOMINA_ROL_PAGO", "Rol de pagos individual", "NOMINA",
            "El rol de pagos de un empleado con el formato de la empresa.", ("mes", "empleado"), (A,), obligatorios=("empleado",),
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
            "Por base: contactos, gestionados, no contestó, no quiere, DAMI sacados y efectividad, con la media de todas.",
            ("base",), (A, C), icono="database-check"),
    Reporte("BASES_GESTION", "Gestión de bases por vendedor", "VENTAS",
            "Contactos enviados a cada vendedor, cuántos gestionó, cuántos le faltan y cómo los categorizó.",
            ("base", "vendedor"), (A, C), icono="person-lines-fill"),
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
]}

# El panel de estadísticas se registra como tipo de reporte para usar los mismos permisos por rol.
ESTADISTICAS = Reporte("VENTAS_ESTADISTICAS", "Estadísticas y proyección de ventas", "VENTAS",
                       "Gráficas de ventas por mes, ranking, equipos y proyecciones del mes y de los próximos meses.",
                       ("mes", "equipo", "vendedor"), (A,), icono="bar-chart-line")

# Para inicializar_datos: (código, nombre, módulo, filtros, roles, descripción)
TIPOS_REPORTE = [(r.codigo, r.nombre, r.modulo, list(r.filtros), list(r.roles), r.descripcion)
                 for r in [*REPORTES.values(), ESTADISTICAS]]
