"""
Operaciones de la contabilidad. Las reglas duras están en la base (migración 0002); aquí se valida antes
para dar mensajes claros y se agrupa lo que tiene que pasar junto (un traspaso son dos movimientos).
"""
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (CERO, Asiento, Cliente, CuentaContable, CuentaFinanciera, Empresa, Lead, LineaAsiento,
                     MovimientoFinanciero, Proveedor, SeguimientoLead)

PLAN_CUENTAS = Path(__file__).parent / "datos" / "plan_cuentas.json"
TIPOS_EXCEL = {"ACTIVO": "ACTIVO", "PASIVO": "PASIVO", "PATRIMONIO": "PATRIMONIO", "INGRESO": "INGRESO",
               "INGRESOS": "INGRESO", "COSTOS": "COSTO", "COSTO": "COSTO", "GASTOS": "GASTO", "GASTO": "GASTO"}


def mensaje_bd(error: DatabaseError) -> str:
    texto = str(getattr(error, "__cause__", None) or error).split("\n")[0]
    return texto.removeprefix("ERROR:").strip()


def _guardar(instancia, **kwargs):
    """save() que convierte los rechazos de la base en un mensaje para la persona."""
    try:
        with transaction.atomic():
            instancia.save(**kwargs)
    except DatabaseError as error:
        raise ValidationError(mensaje_bd(error)) from error
    return instancia


# --------------------------------------------------------------------------- plan de cuentas
def cargar_plan_cuentas() -> int:
    """Crea las cuentas del Excel que falten. No toca las que ya existen (la contadora pudo corregirlas)."""
    filas = json.loads(PLAN_CUENTAS.read_text(encoding="utf-8"))
    existentes = set(CuentaContable.objects.values_list("codigo", flat=True))
    nuevas = [
        CuentaContable(codigo=f["codigo"], nombre=f["nombre"], tipo=TIPOS_EXCEL[f["tipo"].upper()],
                       naturaleza=f["naturaleza"].upper(), descripcion=f.get("descripcion", "")[:255])
        for f in filas if f["codigo"] not in existentes
    ]
    CuentaContable.objects.bulk_create(nuevas)
    return len(nuevas)


def cuentas_de_movimiento(**filtro):
    """Cuentas «hoja» activas: las únicas que se usan en asientos y en cajas y bancos."""
    padres = set()
    for codigo in CuentaContable.objects.values_list("codigo", flat=True):
        partes = codigo.split(".")
        padres.update(".".join(partes[:i]) for i in range(1, len(partes)))
    return CuentaContable.objects.filter(activa=True, **filtro).exclude(codigo__in=padres).order_by("codigo")


# --------------------------------------------------------------------------- códigos correlativos
def siguiente_codigo(empresa: Empresa, modelo, prefijo: str) -> str:
    """CLI-0001, PRV-0001, AF-0001… por empresa. Bloquea la empresa para que dos personas no tomen el mismo."""
    Empresa.objects.select_for_update().get(pk=empresa.pk)
    numeros = [int(c.rsplit("-", 1)[-1]) for c in modelo.objects.filter(empresa=empresa, codigo__startswith=f"{prefijo}-")
               .values_list("codigo", flat=True) if c.rsplit("-", 1)[-1].isdigit()]
    return f"{prefijo}-{max(numeros, default=0) + 1:04d}"


@transaction.atomic
def guardar_con_codigo(instancia, prefijo: str):
    if not instancia.codigo:
        instancia.codigo = siguiente_codigo(instancia.empresa, type(instancia), prefijo)
    try:
        with transaction.atomic():
            instancia.save()
    except IntegrityError as error:
        texto = mensaje_bd(error)
        if "identificacion" in texto:
            raise ValidationError({"identificacion": "Ya hay un registro con esta identificación en la empresa."}) from error
        if "codigo" in texto:
            raise ValidationError({"codigo": "Ese código ya está en uso en la empresa."}) from error
        raise ValidationError(texto) from error
    return instancia


# --------------------------------------------------------------------------- leads y clientes
@transaction.atomic
def registrar_seguimiento(lead: Lead, usuario, nota: str, estado: str, proximo_contacto=None, motivo_perdida: str = ""):
    if not lead.abierto:
        raise ValidationError("El lead ya está cerrado (convertido o perdido).")
    if estado == Lead.Estado.CONVERTIDO:
        raise ValidationError("Para convertirlo use «Convertir en cliente».")
    if estado == Lead.Estado.PERDIDO and not motivo_perdida.strip():
        raise ValidationError({"motivo_perdida": "Indique por qué se perdió."})
    SeguimientoLead.objects.create(lead=lead, nota=nota.strip(), estado=estado, usuario=usuario)
    lead.estado = estado
    lead.proximo_contacto = None if estado == Lead.Estado.PERDIDO else proximo_contacto
    lead.motivo_perdida = motivo_perdida.strip()[:255] if estado == Lead.Estado.PERDIDO else ""
    return _guardar(lead)


@transaction.atomic
def convertir_lead(lead: Lead, cliente: Cliente, usuario) -> Cliente:
    """El lead contrató: se crea su cliente (con los datos que faltaban) y el lead queda cerrado."""
    lead = Lead.objects.select_for_update().get(pk=lead.pk)
    if not lead.abierto:
        raise ValidationError("Este lead ya está cerrado.")
    cliente.empresa, cliente.registrado_por = lead.empresa, usuario
    guardar_con_codigo(cliente, "CLI")
    SeguimientoLead.objects.create(lead=lead, nota=f"Convertido en el cliente {cliente.codigo}.",
                                   estado=Lead.Estado.CONVERTIDO, usuario=usuario)
    lead.estado, lead.cliente, lead.convertido_en, lead.proximo_contacto = Lead.Estado.CONVERTIDO, cliente, timezone.now(), None
    _guardar(lead)
    return cliente


# --------------------------------------------------------------------------- caja y bancos
_SIGNO = Case(When(tipo__in=MovimientoFinanciero.ENTRADAS, then=F("valor")), default=-F("valor"),
              output_field=DecimalField(max_digits=14, decimal_places=2))


def saldo(cuenta: CuentaFinanciera, al: date | None = None) -> Decimal:
    movimientos = cuenta.movimientos.filter(anulado=False)
    if al:
        movimientos = movimientos.filter(fecha__lte=al)
    return cuenta.saldo_inicial + movimientos.aggregate(t=Coalesce(Sum(_SIGNO), Value(CERO)))["t"]


def saldos_por_cuenta(empresa: Empresa) -> list[tuple[CuentaFinanciera, Decimal]]:
    cuentas = CuentaFinanciera.objects.filter(empresa=empresa).select_related("cuenta_contable").annotate(
        movido=Coalesce(Sum(Case(When(movimientos__anulado=False, then=Case(
            When(movimientos__tipo__in=MovimientoFinanciero.ENTRADAS, then=F("movimientos__valor")),
            default=-F("movimientos__valor"))), default=Value(CERO)),
            output_field=DecimalField(max_digits=14, decimal_places=2)), Value(CERO)),
        sin_conciliar=Sum(Case(When(movimientos__anulado=False, movimientos__conciliado=False, then=1), default=0)),
    ).order_by("-activa", "tipo", "nombre")
    return [(c, c.saldo_inicial + c.movido) for c in cuentas]


def _validar_fecha(fecha: date):
    if fecha > timezone.localdate():
        raise ValidationError({"fecha": "La fecha no puede ser futura."})


@transaction.atomic
def registrar_movimiento(usuario, *, cuenta: CuentaFinanciera, fecha: date, tipo: str, concepto: str, valor: Decimal,
                         cliente=None, proveedor=None, tercero: str = "", referencia: str = "", observaciones: str = "",
                         soporte=None, soporte_mime: str = "") -> MovimientoFinanciero:
    if tipo in MovimientoFinanciero.TRASPASOS:
        raise ValidationError({"tipo": "Los traspasos entre cuentas propias se registran con «Traspaso»."})
    _validar_fecha(fecha)
    if cuenta.es_caja and tipo in MovimientoFinanciero.SALIDAS and valor > saldo(cuenta):
        raise ValidationError({"valor": f"Saldo insuficiente: {cuenta.nombre} tiene ${saldo(cuenta):,.2f}."})
    movimiento = MovimientoFinanciero(
        cuenta=cuenta, fecha=fecha, tipo=tipo, concepto=concepto.strip(), valor=valor, cliente=cliente, proveedor=proveedor,
        tercero="" if (cliente or proveedor) else tercero.strip(), referencia=referencia.strip(),
        observaciones=observaciones.strip(), registrado_por=usuario, soporte_mime=soporte_mime if soporte else "")
    if soporte:
        movimiento.soporte = soporte
    return _guardar(movimiento)


@transaction.atomic
def registrar_traspaso(usuario, *, origen: CuentaFinanciera, destino: CuentaFinanciera, fecha: date, valor: Decimal,
                       concepto: str, referencia: str = "") -> MovimientoFinanciero:
    """Dinero que pasa de una cuenta propia a otra (ej.: depositar el efectivo de la caja en el banco)."""
    if origen.pk == destino.pk:
        raise ValidationError({"destino": "Elija una cuenta distinta a la de origen."})
    if origen.empresa_id != destino.empresa_id:
        raise ValidationError({"destino": "Las dos cuentas deben ser de la misma empresa."})
    _validar_fecha(fecha)
    if origen.es_caja and valor > saldo(origen):
        raise ValidationError({"valor": f"Saldo insuficiente: {origen.nombre} tiene ${saldo(origen):,.2f}."})
    comun = {"fecha": fecha, "valor": valor, "concepto": concepto.strip(), "referencia": referencia.strip(),
             "registrado_por": usuario}
    entrada = _guardar(MovimientoFinanciero(cuenta=destino, tipo=MovimientoFinanciero.Tipo.TRASPASO_ENTRADA,
                                            tercero=f"Desde {origen.nombre}"[:150], **comun))
    return _guardar(MovimientoFinanciero(cuenta=origen, tipo=MovimientoFinanciero.Tipo.TRASPASO_SALIDA,
                                         tercero=f"Hacia {destino.nombre}"[:150], traspaso=entrada, **comun))


def _anular_uno(movimiento, usuario, motivo):
    movimiento.anulado, movimiento.motivo_anulacion = True, motivo
    movimiento.anulado_por, movimiento.anulado_en = usuario, timezone.now()
    movimiento.save(update_fields=["anulado", "motivo_anulacion", "anulado_por", "anulado_en", "actualizado_en"])


def anular_movimiento(usuario, movimiento: MovimientoFinanciero, motivo: str) -> MovimientoFinanciero:
    motivo = (motivo or "").strip()[:255]
    if not motivo:
        raise ValidationError("Indique el motivo de la anulación.")
    if movimiento.anulado:
        raise ValidationError("Este movimiento ya está anulado.")
    if movimiento.conciliado:
        raise ValidationError("El movimiento está conciliado con el banco: quite la conciliación antes de anularlo.")
    par = movimiento.contraparte
    if par is not None and par.conciliado:
        raise ValidationError("El otro lado del traspaso está conciliado: quite esa conciliación antes de anularlo.")
    try:
        with transaction.atomic():
            # Primero la salida: si se anula la entrada antes, la caja podría verse en negativo un instante.
            for m in sorted([movimiento] + ([par] if par else []), key=lambda m: m.entra):
                _anular_uno(m, usuario, motivo)
    except DatabaseError as error:
        movimiento.refresh_from_db()
        raise ValidationError(mensaje_bd(error)) from error
    return movimiento


def conciliar(movimiento: MovimientoFinanciero, conciliado: bool, fecha: date | None = None) -> MovimientoFinanciero:
    if movimiento.anulado:
        raise ValidationError("Un movimiento anulado no se concilia.")
    movimiento.conciliado = conciliado
    movimiento.conciliado_en = (fecha or timezone.localdate()) if conciliado else None
    return _guardar(movimiento, update_fields=["conciliado", "conciliado_en", "actualizado_en"])


@dataclass
class FilaEstado:
    movimiento: MovimientoFinanciero
    saldo: Decimal | None  # None en los anulados: no mueven el saldo


def estado_de_cuenta(cuenta: CuentaFinanciera, desde: date | None = None, hasta: date | None = None):
    """Movimientos de la cuenta con el saldo acumulado (como la columna «Saldo» del Excel)."""
    anterior = saldo(cuenta, al=desde - timedelta(days=1)) if desde else cuenta.saldo_inicial
    qs = cuenta.movimientos.select_related("cliente", "proveedor", "registrado_por").order_by("fecha", "pk")
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    filas, acumulado = [], anterior
    for m in qs:
        if not m.anulado:
            acumulado += m.valor if m.entra else -m.valor
        filas.append(FilaEstado(m, None if m.anulado else acumulado))
    return anterior, filas, acumulado


# --------------------------------------------------------------------------- libro diario
def apertura_de(empresa: Empresa) -> Asiento | None:
    return Asiento.objects.filter(empresa=empresa, tipo=Asiento.Tipo.APERTURA).exclude(estado=Asiento.Estado.ANULADO).first()


@transaction.atomic
def guardar_borrador(asiento: Asiento, lineas: list[dict], usuario) -> Asiento:
    """Guarda el asiento en borrador y reemplaza sus líneas. `lineas`: dicts con cuenta, detalle, debe, haber…"""
    if asiento.pk and not asiento.es_borrador:
        raise ValidationError("El asiento ya está contabilizado: no se modifica.")
    if asiento.tipo == Asiento.Tipo.APERTURA:
        otra = apertura_de(asiento.empresa)
        if otra and otra.pk != asiento.pk:
            raise ValidationError({"tipo": f"{asiento.empresa} ya tiene su asiento de apertura (saldos iniciales)."})
    if not asiento.pk:
        asiento.registrado_por = usuario
    _guardar(asiento)
    try:
        with transaction.atomic():
            asiento.lineas.all().delete()
            LineaAsiento.objects.bulk_create([LineaAsiento(asiento=asiento, orden=i, **datos) for i, datos in enumerate(lineas)])
    except DatabaseError as error:
        raise ValidationError(mensaje_bd(error)) from error
    return asiento


def totales(asiento: Asiento) -> tuple[Decimal, Decimal]:
    t = asiento.lineas.aggregate(debe=Coalesce(Sum("debe"), Value(CERO)), haber=Coalesce(Sum("haber"), Value(CERO)))
    return t["debe"], t["haber"]


@transaction.atomic
def contabilizar(asiento: Asiento, usuario) -> Asiento:
    """El asiento pasa al libro: recibe su número correlativo (por empresa) y ya no cambia."""
    Empresa.objects.select_for_update().get(pk=asiento.empresa_id)  # numeración sin saltos ni repetidos
    asiento = Asiento.objects.select_for_update().get(pk=asiento.pk)
    if not asiento.es_borrador:
        raise ValidationError("El asiento ya estaba contabilizado.")
    debe, haber = totales(asiento)
    if debe != haber:
        raise ValidationError(f"El asiento no cuadra: debe ${debe:,.2f} y haber ${haber:,.2f} (diferencia ${debe - haber:,.2f}).")
    ultimo = Asiento.objects.filter(empresa_id=asiento.empresa_id, numero__isnull=False).order_by("-numero").first()
    asiento.numero = (ultimo.numero if ultimo else 0) + 1
    asiento.estado, asiento.contabilizado_por, asiento.contabilizado_en = Asiento.Estado.CONTABILIZADO, usuario, timezone.now()
    return _guardar(asiento)


def anular_asiento(asiento: Asiento, usuario, motivo: str) -> Asiento:
    motivo = (motivo or "").strip()[:255]
    if not motivo:
        raise ValidationError("Indique el motivo de la anulación.")
    if asiento.estado != Asiento.Estado.CONTABILIZADO:
        raise ValidationError("Solo se anulan asientos contabilizados (un borrador se borra).")
    asiento.estado, asiento.motivo_anulacion, asiento.anulado_por, asiento.anulado_en = (
        Asiento.Estado.ANULADO, motivo, usuario, timezone.now())
    return _guardar(asiento)


def borrar_borrador(asiento: Asiento):
    if not asiento.es_borrador:
        raise ValidationError("Solo se borran los borradores; un asiento contabilizado se anula.")
    try:
        with transaction.atomic():
            asiento.delete()
    except DatabaseError as error:
        raise ValidationError(mensaje_bd(error)) from error


# --------------------------------------------------------------------------- balance y mayor
@dataclass
class FilaBalance:
    cuenta: CuentaContable
    debe: Decimal = CERO
    haber: Decimal = CERO
    hijas: list = field(default_factory=list)

    @property
    def saldo_deudor(self) -> Decimal:
        return max(self.debe - self.haber, CERO)

    @property
    def saldo_acreedor(self) -> Decimal:
        return max(self.haber - self.debe, CERO)

    @property
    def nivel(self) -> int:
        return self.cuenta.nivel


def _lineas_contabilizadas(empresa: Empresa, desde: date | None, hasta: date | None):
    qs = LineaAsiento.objects.filter(asiento__empresa=empresa, asiento__estado=Asiento.Estado.CONTABILIZADO)
    if desde:
        qs = qs.filter(asiento__fecha__gte=desde)
    if hasta:
        qs = qs.filter(asiento__fecha__lte=hasta)
    return qs


def balance_comprobacion(empresa: Empresa, desde: date | None = None, hasta: date | None = None):
    """
    Sumas y saldos por cuenta, con los totales subiendo a las cuentas de grupo (1, 1.1, 1.1.01…).
    Solo los asientos contabilizados; incluye la apertura si cae en el rango.
    Devuelve (filas con movimiento, total debe, total haber).
    """
    sumas = {f["cuenta_id"]: f for f in _lineas_contabilizadas(empresa, desde, hasta)
             .values("cuenta_id").annotate(debe=Sum("debe"), haber=Sum("haber"))}
    cuentas = {c.codigo: c for c in CuentaContable.objects.all()}
    filas: dict[str, FilaBalance] = {}
    for cuenta in cuentas.values():
        suma = sumas.get(cuenta.pk)
        if not suma:
            continue
        codigo = cuenta.codigo
        while codigo:  # la cuenta y todos sus grupos
            if codigo in cuentas:
                fila = filas.setdefault(codigo, FilaBalance(cuentas[codigo]))
                fila.debe += suma["debe"]
                fila.haber += suma["haber"]
            codigo = codigo.rpartition(".")[0]
    total_debe = sum((s["debe"] for s in sumas.values()), CERO)
    total_haber = sum((s["haber"] for s in sumas.values()), CERO)
    orden = sorted(filas.values(), key=lambda f: [int(p) for p in f.cuenta.codigo.split(".")])
    return orden, total_debe, total_haber


def libro_mayor(empresa: Empresa, cuenta: CuentaContable, desde: date | None = None, hasta: date | None = None):
    """Movimientos de una cuenta (y sus subcuentas) con el saldo según su naturaleza."""
    filtro = Q(cuenta=cuenta) | Q(cuenta__codigo__startswith=f"{cuenta.codigo}.")
    anterior = CERO
    if desde:
        previo = _lineas_contabilizadas(empresa, None, desde - timedelta(days=1)).filter(filtro).aggregate(
            d=Coalesce(Sum("debe"), Value(CERO)), h=Coalesce(Sum("haber"), Value(CERO)))
        anterior = _con_naturaleza(cuenta, previo["d"], previo["h"])
    lineas = (_lineas_contabilizadas(empresa, desde, hasta).filter(filtro)
              .select_related("asiento", "cuenta", "cliente", "proveedor").order_by("asiento__fecha", "asiento__numero", "orden"))
    filas, acumulado = [], anterior
    for linea in lineas:
        acumulado += _con_naturaleza(cuenta, linea.debe, linea.haber)
        filas.append((linea, acumulado))
    return anterior, filas, acumulado


def _con_naturaleza(cuenta: CuentaContable, debe: Decimal, haber: Decimal) -> Decimal:
    return debe - haber if cuenta.naturaleza == CuentaContable.Naturaleza.DEUDORA else haber - debe


# --------------------------------------------------------------------------- resumen del inicio
def resumen(empresa: Empresa) -> dict:
    hoy = timezone.localdate()
    return {
        "saldos": saldos_por_cuenta(empresa),
        "borradores": Asiento.objects.filter(empresa=empresa, estado=Asiento.Estado.BORRADOR).count(),
        "sin_conciliar": MovimientoFinanciero.objects.filter(cuenta__empresa=empresa, anulado=False, conciliado=False,
                                                             cuenta__tipo__in=["CORRIENTE", "AHORROS"]).count(),
        "leads_abiertos": Lead.objects.filter(empresa=empresa, estado__in=Lead.ABIERTOS).count(),
        "leads_vencidos": Lead.objects.filter(empresa=empresa, estado__in=Lead.ABIERTOS, proximo_contacto__lt=hoy).count(),
        "apertura": apertura_de(empresa),
        "clientes": Cliente.objects.filter(empresa=empresa, activo=True).count(),
        "proveedores": Proveedor.objects.filter(empresa=empresa, activo=True).count(),
    }
