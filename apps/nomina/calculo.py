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

    @property
    def total_descuentos(self) -> Decimal:
        return _dinero(sum((d.valor for d in self.descuentos), Decimal("0")))

    @property
    def total_ingresos(self) -> Decimal:
        return _dinero((self.sueldo_base or 0) + self.comision)

    @property
    def neto(self) -> Decimal:
        return _dinero(self.total_ingresos - self.total_descuentos)

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
            estimacion.descuentos.append(Linea("Falta justificada", monto_justificada, n.fecha))
        elif sueldo:
            estimacion.descuentos.append(Linea(
                f"Falta no justificada ({pct_falta.normalize()} % del sueldo)", _dinero(sueldo * pct_falta / 100), n.fecha,
                pendiente=n.estado == Estado.PENDIENTE,
            ))

    # Atrasos: multa o llamado de atención
    for n in novedades.filter(tipo=Tipo.ATRASO).exclude(estado=Estado.JUSTIFICADA):
        multa = multa_por_atraso(n.minutos or 0, reglas)
        if multa is None:
            estimacion.llamados_mes.append(n)
        else:
            estimacion.descuentos.append(Linea(
                f"Atraso de {n.minutos} min", multa, n.fecha, pendiente=n.estado == Estado.PENDIENTE,
            ))

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
