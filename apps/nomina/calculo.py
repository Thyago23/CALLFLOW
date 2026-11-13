"""
Cálculo de sueldo, comisiones y penalizaciones de un empleado en un mes (SCRUM-14).

Los valores (porcentajes, montos, tramos) no están escritos aquí: se leen de las reglas de
cálculo vigentes (nomina_reglacalculo) y de los esquemas de comisión (Visado, Paquetería),
para que la empresa pueda cambiarlos sin tocar el código. Ver nomina/migrations/0003.

Reglas:
  - Comisión solo para vendedores, sobre el total de ventas cerradas del mes. El porcentaje del
    tramo alcanzado se aplica a TODO el total. Se paga aparte: no entra en el rol de pagos.
  - Falta no justificada: % del sueldo base. Falta justificada: monto fijo.
  - Atraso: multa según los minutos (cada minuto empezado cuenta); pasado el último tramo de
    multa, es llamado de atención. Desde la v2 de las reglas, ese atraso además tiene su propia
    multa (atraso_llamado_multa) y el día cuenta como falta no justificada (no ingresa a la
    oficina). Un atraso justificado no tiene multa ni llamado.
  - Tope (v2): las multas por atraso más las faltas no justificadas del mes no superan el
    porcentaje del sueldo de tope_penalizaciones_porcentaje; lo que pase se descuenta como ajuste.
  - Lo que no está justificado se descuenta: las novedades pendientes de revisar cuentan como
    no justificadas, y se marcan para que el administrador las clasifique antes de pagar.
"""
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q, Sum

CENTAVO = Decimal("0.01")
DIAS_DEL_MES = 30  # mes comercial, como en el rol de pagos de la empresa


def _dinero(valor) -> Decimal:
    return Decimal(valor).quantize(CENTAVO, ROUND_HALF_UP)


def _porcentaje(valor) -> str:
    """10 -> '10' y 12.50 -> '12,5' (normalize() a secas escribiría 10 como '1E+1')."""
    return format(Decimal(valor).normalize(), "f").replace(".", ",")


# --------------------------------------------------------------------------- reglas vigentes
def parametros(codigo: str, fecha: date) -> dict:
    from .models import ReglaCalculo

    regla = (
        ReglaCalculo.objects.filter(codigo=codigo, vigente_desde__lte=fecha)
        .filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=fecha))
        .order_by("-version").first()
    )
    return regla.parametros if regla else {}


def sueldo_base_por_defecto(fecha: date | None = None) -> Decimal:
    monto = parametros("SUELDO_BASE", fecha or date.today()).get("monto")
    return _dinero(monto) if monto else Decimal("400.00")


# --------------------------------------------------------------------------- comisiones
def tramo_alcanzado(esquema, total: Decimal):
    """
    Primer tramo cuyo tope ('hasta', inclusive) cubre el total. Visado: 1.300 → tramo 0 %,
    1.301 → 11 %, 3.000 → 11 %, 3.001 → 15 % (el último tramo no tiene tope).
    """
    for tramo in esquema.tramos.order_by("desde_monto"):
        if tramo.hasta_monto is None or total <= tramo.hasta_monto:
            return tramo
    return None


def comision(esquema, total: Decimal) -> tuple[Decimal, Decimal]:
    """(porcentaje aplicado, monto de la comisión)."""
    if esquema is None or total <= 0:
        return Decimal("0"), Decimal("0.00")
    if esquema.tipo == "PORCENTAJE":
        porcentaje = esquema.porcentaje or Decimal("0")
    elif esquema.tipo == "MONTO_FIJO":
        return Decimal("0"), _dinero(esquema.monto_fijo or 0)
    else:
        tramo = tramo_alcanzado(esquema, total)
        porcentaje = tramo.porcentaje if tramo else Decimal("0")
    return porcentaje, _dinero(total * porcentaje / 100)


# --------------------------------------------------------------------------- atrasos
def multa_por_atraso(minutos: int, reglas: dict) -> Decimal | None:
    """Multa del tramo; None si ya pasó todos los tramos (entonces es llamado de atención)."""
    for tramo in sorted(reglas.get("atraso_multas", []), key=lambda t: t["hasta_minutos"]):
        if minutos <= tramo["hasta_minutos"]:
            return _dinero(tramo["multa"])
    return None


# --------------------------------------------------------------------------- estimación del mes
@dataclass
class Linea:
    concepto: str
    valor: Decimal
    fecha: date | None = None
    pendiente: bool = False  # novedad sin clasificar: se cobra, pero hay que revisarla
    clase: str = ""  # falta_justificada | falta_injustificada | atraso | anticipo


@dataclass
class Estimacion:
    empleado: object
    anio: int
    mes: int
    sueldo_base: Decimal | None
    es_vendedor: bool
    esquema: object
    total_ventas: Decimal
    porcentaje_comision: Decimal
    comision: Decimal
    descuentos: list = field(default_factory=list)
    llamados_mes: list = field(default_factory=list)
    llamados_ventana: int = 0
    llamados_para_visto_bueno: int = 3
    aviso_multas_porcentaje: Decimal = Decimal("10")
    anticipos: list = field(default_factory=list)  # Linea: anticipos aprobados del mes (se descuentan)
    dias_trabajados: int = DIAS_DEL_MES
    cantidad_atrasos: int = 0
    llamado_desde_minutos: int = 11  # atrasos de estos minutos o más son llamado de atención
    tope_porcentaje: Decimal | None = None  # tope de multas por atraso + faltas no justificadas
    ajuste_tope: Decimal = Decimal("0.00")  # lo que se deja de descontar por el tope

    def _de(self, clase):
        return [d for d in self.descuentos if d.clase == clase]

    @property
    def faltas_justificadas(self) -> list:
        return self._de("falta_justificada")

    @property
    def faltas_injustificadas(self) -> list:
        return self._de("falta_injustificada")

    @property
    def atrasos(self) -> list:
        return self._de("atraso")

    @property
    def total_atrasos(self) -> int:
        """Todos los atrasos no justificados del mes: con multa y los que pasaron a llamado de atención."""
        return self.cantidad_atrasos

    @staticmethod
    def suma(lineas) -> Decimal:
        return _dinero(sum((d.valor for d in lineas), Decimal("0")))

    @property
    def total_anticipos(self) -> Decimal:
        return self.suma(self.anticipos)

    @property
    def valor_faltas_justificadas(self) -> Decimal:
        return self.suma(self.faltas_justificadas)

    @property
    def valor_faltas_injustificadas(self) -> Decimal:
        return self.suma(self.faltas_injustificadas)

    @property
    def valor_atrasos(self) -> Decimal:
        return self.suma(self.atrasos)

    @property
    def total_descuentos_y_anticipos(self) -> Decimal:
        return _dinero(self.total_descuentos + self.total_anticipos)

    @property
    def total_descuentos(self) -> Decimal:
        return _dinero(sum((d.valor for d in self.descuentos), Decimal("0")) - self.ajuste_tope)

    @property
    def tope_penalizaciones(self) -> Decimal | None:
        if self.tope_porcentaje is None or not self.sueldo_base:
            return None
        return _dinero(self.sueldo_base * self.tope_porcentaje / 100)

    @property
    def total_ingresos(self) -> Decimal:
        """Ingresos del rol de pagos: la comisión va aparte."""
        return _dinero(self.sueldo_base or 0)

    @property
    def neto(self) -> Decimal:
        """Lo que se recibe por el rol de pagos, sin la comisión."""
        return _dinero(self.total_ingresos - self.total_descuentos - self.total_anticipos)

    @property
    def total_a_pagar(self) -> Decimal:
        """Lo que desembolsa la empresa: el neto del rol más la comisión."""
        return _dinero(self.neto + self.comision)

    @property
    def pendientes(self) -> int:
        return sum(1 for d in self.descuentos if d.pendiente)

    @property
    def requiere_visto_bueno(self) -> bool:
        return self.llamados_ventana >= self.llamados_para_visto_bueno

    @property
    def multas_superan_aviso(self) -> bool:
        if not self.sueldo_base or self.tope_porcentaje is not None:  # con tope, el sistema ya lo respeta
            return False
        return self.total_descuentos > self.sueldo_base * self.aviso_multas_porcentaje / 100


def ultimo_dia(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def estimar_mes(empleado, anio: int, mes: int) -> Estimacion:
    from apps.asistencia.models import NovedadAsistencia
    from apps.core.cuentas import rol_para_cargo
    from apps.core.models import Rol
    from apps.documentos.contratos import salario_vigente
    from apps.personal.models import AsignacionComision, ObservacionEmpleado
    from apps.ventas.models import EstadoProduccion, RegistroProduccion

    inicio, fin = date(anio, mes, 1), ultimo_dia(anio, mes)
    reglas = parametros("PENALIZACIONES", fin)
    sueldo = salario_vigente(empleado, fin)
    es_vendedor = rol_para_cargo(empleado.cargo.nombre) == Rol.VENDEDOR

    # Comisión
    asignacion = (
        AsignacionComision.objects.filter(empleado=empleado, vigente_desde__lte=fin)
        .filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=fin))
        .select_related("esquema").order_by("-vigente_desde").first()
    )
    esquema = asignacion.esquema if asignacion else None
    total_ventas = _dinero(
        RegistroProduccion.objects.filter(
            hoja__vendedor=empleado, hoja__anio=anio, hoja__mes=mes, estado=EstadoProduccion.VENTA_CERRADA,
        ).aggregate(t=Sum("valor_venta"))["t"] or 0
    )
    porcentaje, monto_comision = comision(esquema, total_ventas) if es_vendedor else (Decimal("0"), Decimal("0.00"))

    estimacion = Estimacion(
        empleado=empleado, anio=anio, mes=mes, sueldo_base=sueldo, es_vendedor=es_vendedor, esquema=esquema,
        total_ventas=total_ventas, porcentaje_comision=porcentaje, comision=monto_comision,
        llamados_para_visto_bueno=int(reglas.get("llamados_para_visto_bueno", 3)),
        aviso_multas_porcentaje=Decimal(str(reglas.get("aviso_multas_porcentaje", "10"))),
    )

    Tipo, Estado = NovedadAsistencia.Tipo, NovedadAsistencia.Estado
    novedades = NovedadAsistencia.objects.filter(empleado=empleado, fecha__range=(inicio, fin)).order_by("fecha")

    # Faltas
    pct_falta = Decimal(str(reglas.get("falta_no_justificada_porcentaje", "10")))
    monto_justificada = _dinero(reglas.get("falta_justificada_monto", "13.33"))
    for n in novedades.filter(tipo=Tipo.AUSENCIA):
        if n.estado == Estado.JUSTIFICADA:
            estimacion.descuentos.append(Linea("Falta justificada", monto_justificada, n.fecha, clase="falta_justificada"))
        elif sueldo:
            estimacion.descuentos.append(Linea(
                f"Falta no justificada ({_porcentaje(pct_falta)} % del sueldo)", _dinero(sueldo * pct_falta / 100), n.fecha,
                pendiente=n.estado == Estado.PENDIENTE, clase="falta_injustificada",
            ))

    # Atrasos: multa o llamado de atención (v2: el llamado también tiene multa y cuenta como falta)
    multa_llamado = reglas.get("atraso_llamado_multa")
    llamado_es_falta = bool(reglas.get("atraso_llamado_es_falta"))
    for n in novedades.filter(tipo=Tipo.ATRASO).exclude(estado=Estado.JUSTIFICADA):
        estimacion.cantidad_atrasos += 1
        pendiente = n.estado == Estado.PENDIENTE
        multa = multa_por_atraso(n.minutos or 0, reglas)
        if multa is not None:
            estimacion.descuentos.append(Linea(f"Atraso de {n.minutos} min", multa, n.fecha, pendiente=pendiente, clase="atraso"))
            continue
        estimacion.llamados_mes.append(n)
        if multa_llamado:
            estimacion.descuentos.append(Linea(
                f"Atraso de {n.minutos} min (llamado de atención)", _dinero(multa_llamado), n.fecha, pendiente=pendiente, clase="atraso",
            ))
        if llamado_es_falta and sueldo:
            estimacion.descuentos.append(Linea(
                f"Falta por atraso de {n.minutos} min: no ingresó ({_porcentaje(pct_falta)} % del sueldo)",
                _dinero(sueldo * pct_falta / 100), n.fecha, pendiente=pendiente, clase="falta_injustificada",
            ))

    # Tope: multas por atraso + faltas no justificadas no superan el % del sueldo
    if reglas.get("tope_penalizaciones_porcentaje") is not None:
        estimacion.tope_porcentaje = Decimal(str(reglas["tope_penalizaciones_porcentaje"]))
        tope = estimacion.tope_penalizaciones
        penalizaciones = estimacion.suma(estimacion.atrasos + estimacion.faltas_injustificadas)
        if tope is not None and penalizaciones > tope:
            estimacion.ajuste_tope = _dinero(penalizaciones - tope)

    # Anticipos aprobados de algún periodo de nómina que cae en el mes
    from .models import NovedadNomina

    for a in (NovedadNomina.objects.filter(empleado=empleado, tipo__codigo="ANTICIPO", estado=NovedadNomina.Estado.APROBADA,
                                           periodo__fecha_inicio__lte=fin, periodo__fecha_fin__gte=inicio)
              .select_related("periodo").order_by("periodo__fecha_inicio", "pk")):
        estimacion.anticipos.append(Linea(a.descripcion or "Anticipo", _dinero(a.valor), a.aprobada_en and a.aprobada_en.date(),
                                          clase="anticipo"))

    estimacion.dias_trabajados = dias_trabajados(empleado, anio, mes, len(estimacion.faltas_injustificadas))

    # Llamados de atención acumulados (atrasos largos + llamados registrados a mano en Observaciones)
    desde_ventana = fin - timedelta(days=int(reglas.get("ventana_llamados_dias", 365)))
    ultimo_tramo = max((t["hasta_minutos"] for t in reglas.get("atraso_multas", [])), default=10)
    por_atraso = NovedadAsistencia.objects.filter(
        empleado=empleado, tipo=Tipo.ATRASO, fecha__range=(desde_ventana, fin), minutos__gt=ultimo_tramo,
    ).exclude(estado=Estado.JUSTIFICADA).count()
    manuales = ObservacionEmpleado.objects.filter(
        empleado=empleado, tipo=ObservacionEmpleado.Tipo.LLAMADO_ATENCION, creado_en__date__range=(desde_ventana, fin),
    ).count()
    estimacion.llamados_ventana = por_atraso + manuales
    estimacion.llamado_desde_minutos = ultimo_tramo + 1
    return estimacion


def dias_trabajados(empleado, anio: int, mes: int, faltas_injustificadas: int) -> int:
    """
    Mes comercial de 30 días, menos los días fuera de contrato (ingreso o salida a mitad de
    mes) y las faltas no justificadas (las justificadas se pagan y cuentan como trabajadas).
    """
    inicio, fin = date(anio, mes, 1), ultimo_dia(anio, mes)
    desde = max(inicio, empleado.fecha_ingreso)
    hasta = min(fin, empleado.fecha_salida) if empleado.fecha_salida else fin
    if hasta < desde:
        return 0
    dias = DIAS_DEL_MES
    if desde > inicio:
        dias -= min(desde.day, DIAS_DEL_MES) - 1  # quien entra el 31 trabaja al menos un día
    if hasta < fin:
        dias -= min(DIAS_DEL_MES, fin.day) - hasta.day
    return max(dias - faltas_injustificadas, 0)


# --------------------------------------------------------------------------- nómina de todos
def empleados_del_mes(anio: int, mes: int):
    """Empleados que trabajaron en algún momento del mes (ingresaron antes de que termine y no salieron antes)."""
    from apps.personal.models import Empleado

    inicio, fin = date(anio, mes, 1), ultimo_dia(anio, mes)
    return (
        Empleado.objects.filter(fecha_ingreso__lte=fin)
        .filter(Q(fecha_salida__isnull=True) | Q(fecha_salida__gte=inicio))
        .select_related("cargo").order_by("apellidos", "nombres")
    )


@dataclass
class ResumenNomina:
    roles: list

    def _suma(self, atributo) -> Decimal:
        return _dinero(sum((getattr(r, atributo) or 0 for r in self.roles), Decimal("0")))

    @property
    def total_sueldos(self):
        return self._suma("sueldo_base")

    @property
    def total_comisiones(self):
        return self._suma("comision")

    @property
    def total_ingresos(self):
        return self._suma("total_ingresos")

    @property
    def total_descuentos(self):
        return self._suma("total_descuentos")

    @property
    def total_anticipos(self):
        return self._suma("total_anticipos")

    @property
    def total_descuentos_y_anticipos(self):
        return self._suma("total_descuentos_y_anticipos")

    @property
    def total_neto(self):
        return self._suma("neto")

    @property
    def total_a_pagar(self):
        return self._suma("total_a_pagar")

    @property
    def pendientes(self):
        return sum(r.pendientes for r in self.roles)

    @property
    def sin_sueldo(self):
        return [r for r in self.roles if not r.sueldo_base]

    @property
    def visto_bueno(self):
        return [r for r in self.roles if r.requiere_visto_bueno]


def nomina_del_mes(anio: int, mes: int) -> ResumenNomina:
    return ResumenNomina([estimar_mes(e, anio, mes) for e in empleados_del_mes(anio, mes)])


# --------------------------------------------------------------------------- rol de pagos en PDF
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def nombre_mes(anio: int, mes: int) -> str:
    return f"{MESES[mes - 1]} de {anio}"


_TINTA, _GRIS, _SUAVE, _LINEA = (14, 50, 92), (95, 105, 120), (229, 235, 243), (214, 220, 229)
PIE_ROL = "Mes comercial de 30 días; las faltas y atrasos sin clasificar se cuentan como no justificados."


def _documento_pdf(titulo: str, nombre_documento: str, e: Estimacion):
    """A4 vertical con el encabezado (título, periodo y logo) y los datos del empleado."""
    from django.contrib.staticfiles import finders
    from fpdf import FPDF

    from apps.documentos.contratos import _latin1

    emp = e.empleado
    pdf = FPDF(orientation="P", format="A4")
    pdf.set_title(_latin1(f"{nombre_documento} {nombre_mes(e.anio, e.mes)} - {emp.nombres} {emp.apellidos}"))
    pdf.set_creator("CALLFLOW")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    ancho = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_text_color(*_TINTA)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(ancho - 26, 9, _latin1(titulo), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_GRIS)
    pdf.set_font("Helvetica", size=10.5)
    pdf.cell(ancho - 26, 6, _latin1(f"Periodo: {nombre_mes(e.anio, e.mes)}"), new_x="LMARGIN", new_y="NEXT")
    logo = finders.find("img/logo-multivisa.png")
    if logo:
        pdf.image(logo, x=pdf.w - pdf.r_margin - 18, y=pdf.t_margin - 2, h=18)
    pdf.set_y(pdf.t_margin + 19)
    pdf.set_draw_color(*_TINTA)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + ancho, pdf.get_y())
    pdf.ln(5)

    datos = [("Empleado", f"{emp.nombres} {emp.apellidos}", "Cédula", emp.identificacion),
             ("Cargo", str(emp.cargo), "Equipo", str(e.esquema) if e.esquema else "-")]
    mitad = ancho / 2
    for fila_datos in datos:
        for etiqueta, valor in (fila_datos[:2], fila_datos[2:]):
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*_GRIS)
            pdf.cell(22, 6.5, _latin1(etiqueta))
            pdf.set_font("Helvetica", size=10)
            pdf.set_text_color(30)
            pdf.cell(mitad - 22, 6.5, _latin1(valor))
        pdf.ln()
    pdf.ln(4)
    pdf.set_line_width(0.2)
    pdf.set_draw_color(*_LINEA)
    return pdf, ancho


def _pdf_seccion(pdf, ancho, texto: str):
    from apps.documentos.contratos import _latin1

    pdf.set_fill_color(*_SUAVE)
    pdf.set_text_color(*_TINTA)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(ancho, 6.5, _latin1(f" {texto.upper()}"), fill=True, new_x="LMARGIN", new_y="NEXT")


def _pdf_fila(pdf, ancho, concepto: str, valor: str, negrita=False):
    from apps.documentos.contratos import _latin1

    pdf.set_text_color(30)
    pdf.set_font("Helvetica", "B" if negrita else "", 10)
    pdf.cell(ancho - 36, 7, _latin1(f" {concepto}"), border="B")
    pdf.cell(36, 7, _latin1(f"{valor} "), border="B", align="R", new_x="LMARGIN", new_y="NEXT")


def _pdf_total(pdf, ancho, etiqueta: str, valor: str):
    """El valor final del documento, entre dos líneas gruesas."""
    from apps.documentos.contratos import _latin1

    pdf.ln(1.5)
    y = pdf.get_y()
    pdf.set_draw_color(*_TINTA)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, y, pdf.l_margin + ancho, y)
    pdf.set_text_color(*_TINTA)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(ancho - 45, 11, _latin1(f" {etiqueta}"))
    pdf.cell(45, 11, _latin1(f"{valor} "), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + ancho, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.set_draw_color(*_LINEA)


def _pdf_firmas_y_pie(pdf, ancho, e: Estimacion, nota: str, firma_derecha: str = "Firma Contabilidad"):
    from apps.documentos.contratos import _latin1

    if pdf.get_y() > pdf.h - 62:  # las firmas no quedan cortadas al final de la hoja
        pdf.add_page()
    emp = e.empleado
    pdf.ln(26)
    mitad = ancho / 2
    y = pdf.get_y()
    pdf.set_draw_color(60)
    for x in (pdf.l_margin + mitad * 0.1, pdf.l_margin + mitad * 1.1):
        pdf.line(x, y, x + mitad * 0.8, y)
    pdf.ln(1.5)
    pdf.set_text_color(30)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(mitad, 5.5, "Firma Empleado", align="C")
    pdf.cell(mitad, 5.5, _latin1(firma_derecha), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8.5)
    pdf.set_text_color(*_GRIS)
    pdf.cell(mitad, 5, _latin1(f"{emp.nombres} {emp.apellidos} - {emp.identificacion}"), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(7)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.multi_cell(ancho, 3.8, _latin1(f"Generado por CALLFLOW con las reglas de nómina vigentes. {nota}"), new_x="LMARGIN", new_y="NEXT")


def rol_de_pagos_pdf(e: Estimacion) -> bytes:
    """Rol de pagos: sueldo menos descuentos y anticipos. La comisión va en su propio comprobante."""
    from apps.documentos.contratos import _latin1, dinero

    pdf, ancho = _documento_pdf("ROL DE PAGOS", "Rol de pagos", e)
    _pdf_seccion(pdf, ancho, "Ingresos")
    _pdf_fila(pdf, ancho, "Sueldo base", dinero(e.sueldo_base or Decimal("0")))
    _pdf_fila(pdf, ancho, "Total ingresos", dinero(e.total_ingresos), negrita=True)
    _pdf_seccion(pdf, ancho, "Descuentos")
    for d in e.descuentos:
        _pdf_fila(pdf, ancho, f"{d.concepto} - {d.fecha:%d/%m/%Y}" + (" (sin clasificar)" if d.pendiente else ""), f"- {dinero(d.valor)}")
    if e.ajuste_tope:
        _pdf_fila(pdf, ancho, f"Ajuste por tope de multas y faltas ({_porcentaje(e.tope_porcentaje)} % del sueldo: "
                              f"{dinero(e.tope_penalizaciones)})", f"+ {dinero(e.ajuste_tope)}")
    for a in e.anticipos:
        _pdf_fila(pdf, ancho, "Anticipo" + (f": {a.concepto}" if a.concepto != "Anticipo" else "") + (f" - {a.fecha:%d/%m/%Y}" if a.fecha else ""),
                  f"- {dinero(a.valor)}")
    if not e.descuentos and not e.anticipos:
        _pdf_fila(pdf, ancho, "Sin descuentos", dinero(Decimal("0")))
    _pdf_fila(pdf, ancho, "Total descuentos", f"- {dinero(e.total_descuentos_y_anticipos)}", negrita=True)
    _pdf_total(pdf, ancho, "NETO A RECIBIR", dinero(e.neto))

    pdf.ln(7)
    _pdf_seccion(pdf, ancho, "Asistencia del mes")
    pdf.set_text_color(*_GRIS)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(ancho - 66, 6, "")
    pdf.cell(30, 6, "CANTIDAD", align="R")
    pdf.cell(36, 6, "DESCUENTO ", align="R", new_x="LMARGIN", new_y="NEXT")
    filas = [("Faltas justificadas", len(e.faltas_justificadas), dinero(e.valor_faltas_justificadas)),
             ("Faltas injustificadas", len(e.faltas_injustificadas), dinero(e.valor_faltas_injustificadas)),
             ("Atrasos", e.total_atrasos, dinero(e.valor_atrasos))]
    if e.llamados_mes:
        filas.append(("Llamados de atención por atraso", len(e.llamados_mes), ""))
    filas.append(("Días trabajados", e.dias_trabajados, ""))
    pdf.set_text_color(30)
    pdf.set_font("Helvetica", size=10)
    for concepto, cantidad, valor in filas:
        pdf.cell(ancho - 66, 6.5, _latin1(f" {concepto}"), border="B")
        pdf.cell(30, 6.5, str(cantidad), border="B", align="R")
        pdf.cell(36, 6.5, _latin1(f"{valor} "), border="B", align="R", new_x="LMARGIN", new_y="NEXT")

    _pdf_firmas_y_pie(pdf, ancho, e, PIE_ROL)
    return bytes(pdf.output())


def comprobante_comision_pdf(e: Estimacion) -> bytes:
    """Comprobante de la comisión del mes, separado del rol de pagos."""
    from apps.documentos.contratos import dinero

    pdf, ancho = _documento_pdf("COMPROBANTE DE COMISIÓN", "Comision", e)
    _pdf_seccion(pdf, ancho, "Detalle")
    if e.esquema:
        _pdf_fila(pdf, ancho, "Ventas cerradas del mes", dinero(e.total_ventas))
        if e.esquema.tipo == "MONTO_FIJO":
            _pdf_fila(pdf, ancho, "Modalidad", "Monto fijo")
        else:
            _pdf_fila(pdf, ancho, "Porcentaje aplicado", f"{_porcentaje(e.porcentaje_comision)} %")
    else:
        _pdf_fila(pdf, ancho, "Administrativo: no se calculan comisiones", "")
    _pdf_total(pdf, ancho, "COMISIÓN A RECIBIR", dinero(e.comision))
    _pdf_firmas_y_pie(pdf, ancho, e, "La comisión se calcula sobre las ventas cerradas del mes.")
    return bytes(pdf.output())
