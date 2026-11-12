"""
Caja chica (sección de Nómina, solo administrador).

Sigue el formato de la empresa (Excel «Caja Chica», dos hojas):
- «Caja chica» (registro y control del mes): fondo inicial, total de ingresos, total de egresos y
  saldo actual = fondo inicial + ingresos − egresos, con cada movimiento numerado.
- «Arqueo de caja»: efectivo contado por denominación, total contado y diferencia contra el registro.
Y el Excel «Recibo Caja Chica»: dinero entregado a una persona, lo que gastó y la liquidación (devolución o
reembolso), que mueve la caja.

Las reglas duras (saldo nunca negativo, movimientos, arqueos y filas de recibo inmutables, anulación con motivo,
cálculo del arqueo, límites de la liquidación) las garantiza la base de datos: migraciones nomina/0005, 0007 y 0010.
"""
from dataclasses import dataclass
import calendar
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.utils import timezone

from apps.ventas.templatetags.humanize_cf import dinero

from .models import DENOMINACIONES, ArqueoCaja, LineaRecibo, MovimientoCajaChica, ReciboCaja

Tipo = MovimientoCajaChica.Tipo
CERO = Decimal("0.00")

_CON_SIGNO = Case(
    When(tipo=Tipo.EGRESO, then=-F("valor")),
    default=F("valor"),
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


def saldo_tras(movimiento: MovimientoCajaChica) -> Decimal | None:
    """Saldo de la caja justo después de este movimiento (en el orden del registro: fecha y N.°)."""
    if movimiento.anulado:
        return None
    qs = _vigentes().filter(Q(fecha__lt=movimiento.fecha) | Q(fecha=movimiento.fecha, pk__lte=movimiento.pk))
    return qs.aggregate(s=Sum(_CON_SIGNO, default=Value(CERO)))["s"]


def _mensaje_bd(error: DatabaseError) -> str:
    texto = str(getattr(error, "__cause__", None) or error).split("\n")[0]
    return texto.removeprefix("ERROR:").strip()


def registrar(usuario, *, fecha: date, tipo: str, concepto: str, valor: Decimal, beneficiario: str = "",
              comprobante: str = "", categoria: str = MovimientoCajaChica.Categoria.OTROS,
              forma_pago: str = MovimientoCajaChica.FormaPago.EFECTIVO, recibo=None) -> MovimientoCajaChica:
    if fecha > timezone.localdate():
        raise ValidationError({"fecha": "La fecha no puede ser futura."})
    if tipo == Tipo.EGRESO and valor > saldo():
        raise ValidationError({"valor": f"Saldo insuficiente: la caja tiene ${dinero(saldo())}."})
    try:
        with transaction.atomic():
            return MovimientoCajaChica.objects.create(
                fecha=fecha, tipo=tipo, categoria=categoria, concepto=concepto.strip(), valor=valor,
                beneficiario=beneficiario.strip(), comprobante=comprobante.strip(), forma_pago=forma_pago,
                recibo=recibo, registrado_por=usuario,
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
        return self.movimiento.valor if self.movimiento.entra else None

    @property
    def egreso(self):
        return self.movimiento.valor if self.movimiento.tipo == Tipo.EGRESO else None


@dataclass
class Reporte:
    desde: date
    hasta: date
    saldo_inicial: Decimal  # lo que quedó en caja antes del periodo
    filas: list[Fila]
    total_ingresos: Decimal
    total_egresos: Decimal
    anulados: int
    total_fondos: Decimal = CERO  # asignaciones y reposiciones del fondo en el periodo

    @property
    def fondo_inicial(self) -> Decimal:
        """«Fondo inicial» de la hoja: lo que quedó del mes anterior más el fondo asignado o repuesto este mes."""
        return self.saldo_inicial + self.total_fondos

    @property
    def saldo_final(self) -> Decimal:
        """«Saldo actual» de la hoja = fondo inicial + total ingresos − total egresos."""
        return self.fondo_inicial + self.total_ingresos - self.total_egresos


def rango_del_mes(anio: int, mes: int) -> tuple[date, date]:
    return date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])


def del_mes(anio: int, mes: int, incluir_anulados: bool = True) -> Reporte:
    """Hoja «Caja chica» de un mes: el fondo inicial es el saldo con que cerró el mes anterior."""
    return reporte(*rango_del_mes(anio, mes), incluir_anulados=incluir_anulados)


def reporte(desde: date, hasta: date, incluir_anulados: bool = True) -> Reporte:
    if hasta < desde:
        desde, hasta = hasta, desde
    corriente = saldo(antes_de=desde)
    inicial = corriente
    filas, ingresos, egresos, fondos, anulados = [], CERO, CERO, CERO, 0
    qs = (MovimientoCajaChica.objects.filter(fecha__range=(desde, hasta))
          .select_related("registrado_por", "anulado_por").order_by("fecha", "pk"))
    for m in qs:
        if m.tipo == Tipo.FONDO:  # el fondo va en su celda «Fondo inicial», no como fila de la hoja
            if not m.anulado:
                corriente += m.valor
                fondos += m.valor
            continue
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
    return Reporte(desde, hasta, inicial, filas, ingresos, egresos, anulados, fondos)


# --------------------------------------------------------------------------- fondo inicial (celda editable de la hoja)
def fijar_fondo_inicial(usuario, anio: int, mes: int, valor: Decimal) -> Reporte:
    """
    La celda «Fondo inicial» del mes se escribe como en el Excel. Por dentro se guarda como una asignación del
    fondo (tipo FONDO) el día 1: lo que falta sobre lo que quedó del mes anterior. Si se corrige, la asignación
    anterior se anula con motivo (queda en la auditoría) y se registra la nueva.
    """
    primero, _ = rango_del_mes(anio, mes)
    if primero > timezone.localdate():
        raise ValidationError("No se puede fijar el fondo de un mes que todavía no empieza.")
    if valor is None or valor < 0:
        raise ValidationError("El fondo inicial no puede ser negativo.")
    valor = Decimal(valor).quantize(Decimal("0.01"))
    arrastre = saldo(antes_de=primero)
    if valor < arrastre:
        raise ValidationError(f"El fondo inicial no puede ser menor que lo que quedó del mes anterior (${dinero(arrastre)}). "
                              f"Si se sacó dinero de la caja, regístrelo como egreso.")
    anteriores = list(_vigentes().filter(tipo=Tipo.FONDO, fecha__range=rango_del_mes(anio, mes)))
    if sum((m.valor for m in anteriores), CERO) == valor - arrastre:
        return del_mes(anio, mes)  # no cambió
    try:
        with transaction.atomic():
            # Primero la nueva asignación y después se anulan las anteriores: así el saldo nunca baja de cero a mitad.
            if valor > arrastre:
                MovimientoCajaChica.objects.create(
                    fecha=primero, tipo=Tipo.FONDO, concepto=f"Fondo inicial: ${dinero(valor)}", valor=valor - arrastre,
                    registrado_por=usuario)
            for m in anteriores:
                m.anulado, m.motivo_anulacion = True, f"Fondo inicial cambiado a ${dinero(valor)}"
                m.anulado_por, m.anulado_en = usuario, timezone.now()
                m.save(update_fields=["anulado", "motivo_anulacion", "anulado_por", "anulado_en", "actualizado_en"])
    except DatabaseError as error:
        raise ValidationError("Con ese fondo inicial el saldo de la caja quedaría negativo por los gastos ya registrados.") from error
    return del_mes(anio, mes)


# --------------------------------------------------------------------------- arqueo
def conteo_limpio(cantidades: dict) -> dict:
    """{"20": "3", "5": "", "0.25": 0} → {"20": 3}: solo denominaciones válidas con cantidad mayor que cero."""
    conteo = {}
    for d in DENOMINACIONES:
        texto = str(cantidades.get(d, "") or "0").strip()
        try:
            cantidad = Decimal(texto)
        except InvalidOperation as error:
            raise ValidationError(f"La cantidad de ${d} no es un número.") from error
        if cantidad < 0 or cantidad != cantidad.to_integral_value() or cantidad > 999999:
            raise ValidationError(f"La cantidad de ${d} debe ser un número entero entre 0 y 999.999.")
        if cantidad:
            conteo[d] = int(cantidad)
    return conteo


def registrar_arqueo(usuario, *, fecha: date, cantidades: dict, observaciones: str = "", responsable=None) -> ArqueoCaja:
    """La base de datos calcula el saldo según registro, el total contado y la diferencia."""
    if fecha > timezone.localdate():
        raise ValidationError({"fecha": "La fecha del arqueo no puede ser futura."})
    try:
        with transaction.atomic():
            arqueo = ArqueoCaja.objects.create(fecha=fecha, responsable=responsable or usuario, conteo=conteo_limpio(cantidades),
                                               observaciones=observaciones.strip(), registrado_por=usuario)
    except DatabaseError as error:
        raise ValidationError(_mensaje_bd(error)) from error
    arqueo.refresh_from_db()  # trae los valores que calculó el trigger
    return arqueo


# --------------------------------------------------------------------------- recibos (entregas a rendir)
def crear_recibo(usuario, *, fecha: date, entregado_a: str, area: str = "", autorizado_por: str = "",
                 observaciones: str = "") -> ReciboCaja:
    if fecha > timezone.localdate():
        raise ValidationError({"fecha": "La fecha del recibo no puede ser futura."})
    if not entregado_a.strip():
        raise ValidationError({"entregado_a": "Indique a quién se entrega el dinero."})
    return ReciboCaja.objects.create(fecha=fecha, entregado_a=entregado_a.strip(), area=area.strip(),
                                     autorizado_por=autorizado_por.strip(), observaciones=observaciones.strip(),
                                     responsable=usuario)


def actualizar_recibo(recibo: ReciboCaja, **datos) -> ReciboCaja:
    """Datos de cabecera (solo con el recibo abierto) y observaciones (siempre)."""
    campos = ["observaciones"] + (["fecha", "entregado_a", "area", "autorizado_por"] if recibo.abierto else [])
    cambiados = [c for c in campos if c in datos]
    for campo in cambiados:
        valor = datos[campo]
        setattr(recibo, campo, valor.strip() if isinstance(valor, str) else valor)
    if not recibo.entregado_a:
        raise ValidationError({"entregado_a": "Indique a quién se entrega el dinero."})
    if recibo.fecha > timezone.localdate():
        raise ValidationError({"fecha": "La fecha del recibo no puede ser futura."})
    recibo.save(update_fields=cambiados + ["actualizado_en"])
    return recibo


def agregar_linea(usuario, recibo: ReciboCaja, *, fecha: date, concepto: str, comprobante: str = "",
                  valor_entregado: Decimal | None = None, valor_gastado: Decimal | None = None) -> LineaRecibo:
    """Lo entregado sale de la caja en ese momento (egreso); lo gastado es el detalle que rinde la persona."""
    valor_entregado, valor_gastado = valor_entregado or CERO, valor_gastado or CERO
    if not recibo.abierto:
        raise ValidationError("El recibo ya está liquidado: no se pueden agregar filas.")
    if valor_entregado <= 0 and valor_gastado <= 0:
        raise ValidationError("Escriba el valor entregado o el valor gastado.")
    if fecha > timezone.localdate():
        raise ValidationError({"fecha": "La fecha no puede ser futura."})
    with transaction.atomic():
        movimiento = None
        if valor_entregado > 0:
            movimiento = registrar(usuario, fecha=fecha, tipo=Tipo.EGRESO, valor=valor_entregado, recibo=recibo,
                                   concepto=f"Recibo N.º {recibo.numero}: {concepto.strip()}"[:200],
                                   beneficiario=recibo.entregado_a, comprobante=comprobante)
        try:
            return LineaRecibo.objects.create(recibo=recibo, fecha=fecha, concepto=concepto.strip(), comprobante=comprobante.strip(),
                                              valor_entregado=valor_entregado, valor_gastado=valor_gastado, movimiento=movimiento)
        except DatabaseError as error:
            raise ValidationError(_mensaje_bd(error)) from error


def anular_linea(usuario, linea: LineaRecibo) -> LineaRecibo:
    """Quita una fila del recibo abierto; si era una entrega, su egreso se anula y el dinero vuelve a la caja."""
    if linea.anulada:
        raise ValidationError("Esta fila ya está anulada.")
    if not linea.recibo.abierto:
        raise ValidationError("El recibo ya está liquidado: no se pueden quitar filas.")
    with transaction.atomic():
        if linea.movimiento_id:
            anular(usuario, linea.movimiento, f"Fila quitada del recibo N.º {linea.recibo.numero}")
        linea.anulada = True
        try:
            linea.save(update_fields=["anulada", "actualizado_en"])
        except DatabaseError as error:
            raise ValidationError(_mensaje_bd(error)) from error
    return linea


def liquidar_recibo(usuario, recibo: ReciboCaja, valor_devuelto: Decimal | None) -> ReciboCaja:
    """
    Cierra el recibo: lo que la persona devolvió entra a la caja y, si gastó más de lo entregado, se le paga el
    valor adicional (sale de la caja). Si devolvió menos de lo que debía, queda saldo pendiente de devolución.
    """
    if not recibo.abierto:
        raise ValidationError("El recibo ya está liquidado.")
    if not recibo._lineas_vigentes():
        raise ValidationError("El recibo no tiene filas para liquidar.")
    valor_devuelto = (valor_devuelto or CERO).quantize(Decimal("0.01"))
    if valor_devuelto < 0 or valor_devuelto > recibo.valor_a_devolver:
        raise ValidationError(f"El valor devuelto debe estar entre $0,00 y el valor a devolver (${dinero(recibo.valor_a_devolver)}).")
    hoy = timezone.localdate()
    try:
        with transaction.atomic():
            if valor_devuelto > 0:
                registrar(usuario, fecha=hoy, tipo=Tipo.INGRESO, valor=valor_devuelto, recibo=recibo, beneficiario=recibo.entregado_a,
                          concepto=f"Recibo N.º {recibo.numero}: devolución de {recibo.entregado_a}"[:200])
            reembolso = recibo.valor_a_reembolsar
            if reembolso > 0:
                registrar(usuario, fecha=hoy, tipo=Tipo.EGRESO, valor=reembolso, recibo=recibo, beneficiario=recibo.entregado_a,
                          concepto=f"Recibo N.º {recibo.numero}: valor adicional reembolsado"[:200])
            recibo.estado, recibo.valor_devuelto, recibo.valor_reembolsado = ReciboCaja.Estado.LIQUIDADO, valor_devuelto, reembolso
            recibo.liquidado_en = timezone.now()
            recibo.save(update_fields=["estado", "valor_devuelto", "valor_reembolsado", "liquidado_en", "actualizado_en"])
    except DatabaseError as error:
        recibo.refresh_from_db()
        raise ValidationError(_mensaje_bd(error)) from error
    except ValidationError:
        recibo.refresh_from_db()
        raise
    return recibo


def registrar_devolucion(usuario, recibo: ReciboCaja, valor: Decimal | None) -> ReciboCaja:
    """Saldo pendiente de devolución que la persona entrega después de liquidar."""
    if recibo.abierto:
        raise ValidationError("Primero liquide el recibo.")
    valor = (valor or CERO).quantize(Decimal("0.01"))
    if valor <= 0 or valor > recibo.saldo_pendiente:
        raise ValidationError(f"La devolución debe estar entre $0,01 y el saldo pendiente (${dinero(recibo.saldo_pendiente)}).")
    try:
        with transaction.atomic():
            registrar(usuario, fecha=timezone.localdate(), tipo=Tipo.INGRESO, valor=valor, recibo=recibo,
                      beneficiario=recibo.entregado_a, concepto=f"Recibo N.º {recibo.numero}: devolución de {recibo.entregado_a}"[:200])
            recibo.valor_devuelto += valor
            recibo.save(update_fields=["valor_devuelto", "actualizado_en"])
    except DatabaseError as error:
        recibo.refresh_from_db()
        raise ValidationError(_mensaje_bd(error)) from error
    return recibo
