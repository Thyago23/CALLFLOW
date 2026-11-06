"""
Caja chica (sección de Nómina, solo administrador).

Las reglas duras (saldo nunca negativo, movimientos inmutables, anulación con motivo) las
garantiza la base de datos: ver migración nomina/0005. Aquí: registro, anulación y el
reporte para imprimir con saldo inicial, saldo acumulado y saldo final.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Case, DecimalField, F, Sum, Value, When
from django.utils import timezone

from apps.ventas.templatetags.humanize_cf import dinero

from .models import MovimientoCajaChica

Tipo = MovimientoCajaChica.Tipo
CERO = Decimal("0.00")

_CON_SIGNO = Case(
    When(tipo=Tipo.INGRESO, then=F("valor")),
    default=-F("valor"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


def _vigentes():
    return MovimientoCajaChica.objects.filter(anulado=False)


def saldo(hasta: date | None = None, antes_de: date | None = None) -> Decimal:
    """Saldo con los movimientos no anulados hasta una fecha (inclusive) o antes de una fecha."""
    qs = _vigentes()
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    if antes_de:
        qs = qs.filter(fecha__lt=antes_de)
    return qs.aggregate(s=Sum(_CON_SIGNO, default=Value(CERO)))["s"]


def _mensaje_bd(error: DatabaseError) -> str:
    texto = str(getattr(error, "__cause__", None) or error).split("\n")[0]
    return texto.removeprefix("ERROR:").strip()


def registrar(usuario, *, fecha: date, tipo: str, concepto: str, valor: Decimal, beneficiario: str = "",
              comprobante: str = "") -> MovimientoCajaChica:
    if fecha > timezone.localdate():
        raise ValidationError({"fecha": "La fecha no puede ser futura."})
    if tipo == Tipo.EGRESO and valor > saldo():
        raise ValidationError({"valor": f"Saldo insuficiente: la caja tiene ${dinero(saldo())}."})
    try:
        with transaction.atomic():
            return MovimientoCajaChica.objects.create(
                fecha=fecha, tipo=tipo, concepto=concepto.strip(), valor=valor, beneficiario=beneficiario.strip(),
                comprobante=comprobante.strip(), registrado_por=usuario,
            )
    except DatabaseError as error:  # p. ej. otro gasto se registró al mismo tiempo y ya no alcanza
        raise ValidationError(_mensaje_bd(error)) from error


def anular(usuario, movimiento: MovimientoCajaChica, motivo: str) -> MovimientoCajaChica:
    if not motivo.strip():
        raise ValidationError("Indique el motivo de la anulación.")
    if movimiento.anulado:
        raise ValidationError("Este movimiento ya está anulado.")
    movimiento.anulado, movimiento.motivo_anulacion = True, motivo.strip()[:255]
    movimiento.anulado_por, movimiento.anulado_en = usuario, timezone.now()
    try:
        with transaction.atomic():
            movimiento.save(update_fields=["anulado", "motivo_anulacion", "anulado_por", "anulado_en", "actualizado_en"])
    except DatabaseError as error:  # anular un ingreso que ya se gastó
        movimiento.refresh_from_db()
        raise ValidationError(_mensaje_bd(error)) from error
    return movimiento


# --------------------------------------------------------------------------- reporte
@dataclass
class Fila:
    movimiento: MovimientoCajaChica
    saldo: Decimal | None  # None en los anulados: no mueven el saldo

    @property
    def ingreso(self):
        return self.movimiento.valor if self.movimiento.tipo == Tipo.INGRESO else None

    @property
    def egreso(self):
        return self.movimiento.valor if self.movimiento.tipo == Tipo.EGRESO else None


@dataclass
class Reporte:
    desde: date
    hasta: date
    saldo_inicial: Decimal
    filas: list[Fila]
    total_ingresos: Decimal
    total_egresos: Decimal
    anulados: int

    @property
    def saldo_final(self) -> Decimal:
        return self.saldo_inicial + self.total_ingresos - self.total_egresos


def reporte(desde: date, hasta: date, incluir_anulados: bool = True) -> Reporte:
    if hasta < desde:
        desde, hasta = hasta, desde
    corriente = saldo(antes_de=desde)
    inicial = corriente
    filas, ingresos, egresos, anulados = [], CERO, CERO, 0
    qs = (MovimientoCajaChica.objects.filter(fecha__range=(desde, hasta))
          .select_related("registrado_por", "anulado_por").order_by("fecha", "pk"))
    for m in qs:
        if m.anulado:
            anulados += 1
            if incluir_anulados:
                filas.append(Fila(m, None))
            continue
        if m.tipo == Tipo.INGRESO:
            corriente += m.valor
            ingresos += m.valor
        else:
            corriente -= m.valor
            egresos += m.valor
        filas.append(Fila(m, corriente))
    return Reporte(desde, hasta, inicial, filas, ingresos, egresos, anulados)
