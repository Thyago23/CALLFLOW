"""
Cálculo de sueldo, comisiones y penalizaciones de un empleado en un mes (SCRUM-14).

Los valores (porcentajes, montos, tramos) no están escritos aquí: se leen de las reglas de
cálculo vigentes (nomina_reglacalculo) y de los esquemas de comisión (Visas, Baymont Travel),
para que la empresa pueda cambiarlos sin tocar el código. Ver nomina/migrations/0003.

Reglas:
  - Comisión solo para vendedores, sobre el total de ventas cerradas del mes. El porcentaje del
    tramo alcanzado se aplica a TODO el total y se suma al sueldo base.
  - Falta no justificada: % del sueldo base. Falta justificada: monto fijo.
  - Atraso: multa según los minutos (cada minuto empezado cuenta); pasado el último tramo de
    multa, es llamado de atención (sin multa). Un atraso justificado no tiene multa ni llamado.
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
    Primer tramo cuyo tope ('hasta', inclusive) cubre el total. Visas: 1.300 → tramo 0 %,
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
        """Atrasos con multa más los que pasaron a llamado de atención."""
        return len(self.atrasos) + len(self.llamados_mes)

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
        return _dinero(sum((d.valor for d in self.descuentos), Decimal("0")))

    @property
    def total_ingresos(self) -> Decimal:
        return _dinero((self.sueldo_base or 0) + self.comision)

    @property
    def neto(self) -> Decimal:
        return _dinero(self.total_ingresos - self.total_descuentos - self.total_anticipos)

    @property
    def pendientes(self) -> int:
        return sum(1 for d in self.descuentos if d.pendiente)

    @property
    def requiere_visto_bueno(self) -> bool:
        return self.llamados_ventana >= self.llamados_para_visto_bueno

    @property
    def multas_superan_aviso(self) -> bool:
        if not self.sueldo_base:
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
                f"Falta no justificada ({pct_falta.normalize()} % del sueldo)", _dinero(sueldo * pct_falta / 100), n.fecha,
                pendiente=n.estado == Estado.PENDIENTE, clase="falta_injustificada",
            ))

    # Atrasos: multa o llamado de atención
    for n in novedades.filter(tipo=Tipo.ATRASO).exclude(estado=Estado.JUSTIFICADA):
        multa = multa_por_atraso(n.minutos or 0, reglas)
        if multa is None:
            estimacion.llamados_mes.append(n)
        else:
            estimacion.descuentos.append(Linea(
                f"Atraso de {n.minutos} min", multa, n.fecha, pendiente=n.estado == Estado.PENDIENTE, clase="atraso",
            ))

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


def nombre_mes(anio: int, mes: int) -> str:
    return f"{MESES[mes - 1]} de {anio}"


def rol_de_pagos_pdf(e: Estimacion) -> bytes:
    """Rol de pagos con el formato de la empresa (franja azul y verde, A4 horizontal) y el detalle de CALLFLOW."""
    from django.contrib.staticfiles import finders
    from fpdf import FPDF

    from apps.documentos.contratos import _latin1, dinero

    azul, verde, gris = (47, 117, 181), (112, 173, 71), (238, 241, 246)
    emp = e.empleado
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_title(_latin1(f"Rol de pagos {nombre_mes(e.anio, e.mes)} - {emp.nombres} {emp.apellidos}"))
    pdf.set_creator("CALLFLOW")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(False)
    pdf.add_page()
    ancho = pdf.w - pdf.l_margin - pdf.r_margin

    # Franja del título
    pdf.set_fill_color(*azul)
    pdf.set_text_color(255)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(ancho, 11, "ROL DE PAGOS", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
    logo = finders.find("img/logo-multivisa.png")
    if logo:
        pdf.image(logo, x=pdf.l_margin + 2, y=pdf.t_margin + 0.5, h=10)

    # Encabezados azul / verde y la fila de datos
    columnas = [  # (título, ancho relativo, color, valor)
        ("Año", 7, azul, str(e.anio)),
        ("Mes", 12, azul, MESES[e.mes - 1].upper()),
        ("Cédula", 12, verde, emp.identificacion),
        ("Trabajador", 21, verde, f"{emp.nombres} {emp.apellidos}"),
        ("Cargo", 16, azul, str(emp.cargo)),
        ("Sueldo Base", 10, verde, dinero(e.sueldo_base) if e.sueldo_base else "Sin sueldo"),
        ("Comisión", 9, azul, dinero(e.comision) if e.es_vendedor else "-"),
        ("Anticipos", 9, verde, dinero(e.total_anticipos)),
        ("TOTAL", 10, verde, dinero(e.neto)),
    ]
    unidad = ancho / sum(c[1] for c in columnas)
    pdf.set_font("Helvetica", "B", 10)
    for titulo, w, color, _ in columnas:
        pdf.set_fill_color(*color)
        pdf.cell(w * unidad, 8, _latin1(titulo), align="C", fill=True)
    pdf.ln()
    pdf.set_text_color(0)
    for i, (_, w, _, valor) in enumerate(columnas):
        pdf.set_font("Helvetica", "B" if i == len(columnas) - 1 else "", 10)
        pdf.cell(w * unidad, 9, _latin1(valor), border="B", align="C")
    pdf.ln(15)

    # Izquierda: faltas, atrasos y días (como el rol de la empresa). Derecha: detalle del cálculo.
    y0 = pdf.get_y()
    x_izq, w_izq = pdf.l_margin, ancho * 0.38
    x_der, w_der = pdf.l_margin + ancho * 0.44, ancho * 0.56
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(110)
    pdf.set_x(x_izq)
    pdf.cell(w_izq * 0.6, 6, "")
    pdf.cell(w_izq * 0.18, 6, "CANTIDAD", align="C")
    pdf.cell(w_izq * 0.22, 6, "DESCUENTO", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.set_font("Helvetica", size=10.5)
    for concepto, cantidad, valor in [
        ("Faltas Justificadas", len(e.faltas_justificadas), dinero(e.valor_faltas_justificadas)),
        ("Faltas Injustificadas", len(e.faltas_injustificadas), dinero(e.valor_faltas_injustificadas)),
        ("Atrasos", e.total_atrasos, dinero(e.valor_atrasos)),
        ("Días Trabajados", e.dias_trabajados, ""),
    ]:
        pdf.set_x(x_izq)
        pdf.cell(w_izq * 0.6, 7, _latin1(concepto), align="R")
        pdf.cell(w_izq * 0.18, 7, str(cantidad), align="C")
        pdf.cell(w_izq * 0.22, 7, _latin1(valor), align="C", new_x="LMARGIN", new_y="NEXT")
    y_izq = pdf.get_y()

    pdf.set_y(y0)

    def fila(concepto, valor, negrita=False, seccion=False):
        pdf.set_x(x_der)
        pdf.set_font("Helvetica", "B" if negrita or seccion else "", 8.5 if seccion else 9.5)
        pdf.set_fill_color(*gris)
        pdf.cell(w_der - 32, 6, _latin1(concepto), border="B", fill=seccion)
        pdf.cell(32, 6, _latin1(valor), border="B", align="R", fill=seccion, new_x="LMARGIN", new_y="NEXT")

    fila("INGRESOS", "", seccion=True)
    fila("Sueldo base", dinero(e.sueldo_base or Decimal("0")))
    if e.es_vendedor:
        fila(f"Comisión {e.esquema} ({e.porcentaje_comision.normalize()} % de {dinero(e.total_ventas)} vendidos)"
             if e.esquema else "Comisión (sin equipo asignado)", dinero(e.comision))
    fila("Total ingresos", dinero(e.total_ingresos), negrita=True)
    fila("DESCUENTOS", "", seccion=True)
    lineas = [(f"{d.concepto} - {d.fecha:%d/%m/%Y}" + (" (sin clasificar)" if d.pendiente else ""), d.valor) for d in e.descuentos]
    lineas += [("Anticipo" + (f": {a.concepto}" if a.concepto != "Anticipo" else ""), a.valor) for a in e.anticipos]
    for concepto, valor in lineas[:9]:
        fila(concepto, f"- {dinero(valor)}")
    if len(lineas) > 9:  # una sola hoja: el resto se resume
        fila(f"Otros {len(lineas) - 9} descuentos", f"- {dinero(sum((v for _, v in lineas[9:]), Decimal('0')))}")
    if not lineas:
        fila("Sin descuentos", dinero(Decimal("0")))
    fila("Total descuentos", f"- {dinero(e.total_descuentos_y_anticipos)}", negrita=True)
    pdf.set_x(x_der)
    pdf.set_font("Helvetica", "B", 11.5)
    pdf.cell(w_der - 32, 9, "TOTAL A RECIBIR")
    pdf.cell(32, 9, _latin1(dinero(e.neto)), align="R", new_x="LMARGIN", new_y="NEXT")

    # Firmas
    pdf.set_y(max(y_izq, pdf.get_y()) + 22)
    pdf.set_font("Helvetica", size=10)
    mitad = ancho / 2
    for x in (pdf.l_margin + mitad * 0.15, pdf.l_margin + mitad * 1.15):
        pdf.line(x, pdf.get_y(), x + mitad * 0.7, pdf.get_y())
    pdf.ln(1.5)
    pdf.cell(mitad, 6, "Firma Empleado", align="C")
    pdf.cell(mitad, 6, "Firma Contabilidad", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8.5)
    pdf.cell(mitad, 5, _latin1(f"{emp.nombres} {emp.apellidos} - {emp.identificacion}"), align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(-16)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(110)
    pdf.cell(0, 4, _latin1("Generado por CALLFLOW con las reglas de nómina vigentes. Mes comercial de 30 días; "
                           "las faltas y atrasos sin clasificar se cuentan como no justificados."))
    return bytes(pdf.output())
