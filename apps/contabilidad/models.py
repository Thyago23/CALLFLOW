"""
Contabilidad de Multivisas y T&T (rol Contador).

Sigue el Excel «Multivisas_Sistema_Control_Integral»: plan de cuentas, saldos iniciales, caja y bancos,
clientes, proveedores, activos fijos y libro diario. Todo (menos el plan de cuentas, que es uno solo)
pertenece a una empresa: lo de Multivisas y lo de T&T no se mezcla en saldos ni en balances.

Es un CRM y un control interno: no emite comprobantes ni se conecta con el SRI.
Los asientos del libro diario los registra la contadora a mano (no se generan solos desde caja y bancos).

Las reglas duras (asiento cuadrado al contabilizar, asientos y movimientos que ya no se pueden cambiar,
caja que no queda en negativo, todo de la misma empresa) las garantiza la base de datos: migración 0002.
"""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now

from apps.core.models import ModeloBase

DINERO = {"max_digits": 14, "decimal_places": 2}
CERO = Decimal("0.00")


def ruta_soporte(instancia, nombre):
    """Nombre aleatorio: la ruta del archivo no dice de quién ni de qué es."""
    import uuid

    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else "pdf"
    return f"contabilidad/soportes/{uuid.uuid4().hex}.{extension}"


# --------------------------------------------------------------------------- empresas
class Empresa(ModeloBase):
    """Multivisas y T&T. Las crea la migración 0003; la contadora trabaja en una a la vez."""

    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=80, unique=True)
    razon_social = models.CharField("razón social", max_length=150, blank=True, default="")
    ruc = models.CharField("RUC", max_length=13, blank=True, default="")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "empresa"
        verbose_name_plural = "empresas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


# --------------------------------------------------------------------------- plan de cuentas
class CuentaContable(ModeloBase):
    """Una cuenta del plan (compartido por las dos empresas). Solo las cuentas sin subcuentas reciben movimientos."""

    class Tipo(models.TextChoices):
        ACTIVO = "ACTIVO", "Activo"
        PASIVO = "PASIVO", "Pasivo"
        PATRIMONIO = "PATRIMONIO", "Patrimonio"
        INGRESO = "INGRESO", "Ingresos"
        COSTO = "COSTO", "Costos"
        GASTO = "GASTO", "Gastos"

    class Naturaleza(models.TextChoices):
        DEUDORA = "DEUDORA", "Deudora"
        ACREEDORA = "ACREEDORA", "Acreedora"

    codigo = models.CharField("código", max_length=30, unique=True)
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    naturaleza = models.CharField(max_length=9, choices=Naturaleza.choices)
    descripcion = models.CharField("uso / observaciones", max_length=255, blank=True, default="")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "cuenta contable"
        verbose_name_plural = "plan de cuentas"
        ordering = ["codigo"]
        constraints = [
            models.CheckConstraint(condition=Q(codigo__regex=r"^[0-9]+(\.[0-9]+)*$"), name="cuenta_codigo_formato"),
            models.CheckConstraint(condition=Q(tipo__in=["ACTIVO", "PASIVO", "PATRIMONIO", "INGRESO", "COSTO", "GASTO"]),
                                   name="cuenta_tipo_valido"),
            models.CheckConstraint(condition=Q(naturaleza__in=["DEUDORA", "ACREEDORA"]), name="cuenta_naturaleza_valida"),
        ]

    @property
    def nivel(self) -> int:
        return self.codigo.count(".") + 1

    @property
    def codigo_padre(self) -> str:
        return self.codigo.rpartition(".")[0]

    @property
    def es_de_movimiento(self) -> bool:
        """Cuenta «hoja»: no tiene subcuentas, así que es la que se usa en los asientos."""
        return not CuentaContable.objects.filter(codigo__startswith=f"{self.codigo}.").exists()

    def __str__(self):
        return f"{self.codigo} {self.nombre}"


# --------------------------------------------------------------------------- terceros
class TipoIdentificacion(models.TextChoices):
    CEDULA = "CEDULA", "Cédula"
    RUC = "RUC", "RUC"
    PASAPORTE = "PASAPORTE", "Pasaporte"
    EXTERIOR = "EXTERIOR", "Identificación del exterior"


class Linea(models.TextChoices):
    """Línea de negocio: los clientes son solo de visas y paquetes (no los del call)."""

    VISAS = "VISAS", "Visas"
    PAQUETES = "PAQUETES", "Paquetes turísticos"
    AMBAS = "AMBAS", "Visas y paquetes"


class Lead(ModeloBase):
    """Persona interesada (redes, WhatsApp, referidos). Cuando contrata, se convierte en cliente."""

    class Interes(models.TextChoices):
        VISA = "VISA", "Visa"
        PAQUETE = "PAQUETE", "Paquete turístico"

    class Origen(models.TextChoices):
        FACEBOOK = "FACEBOOK", "Facebook"
        INSTAGRAM = "INSTAGRAM", "Instagram"
        TIKTOK = "TIKTOK", "TikTok"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        WEB = "WEB", "Página web"
        REFERIDO = "REFERIDO", "Referido"
        OFICINA = "OFICINA", "Visita a la oficina"
        OTRO = "OTRO", "Otro"

    class Estado(models.TextChoices):
        NUEVO = "NUEVO", "Nuevo"
        CONTACTADO = "CONTACTADO", "Contactado"
        SEGUIMIENTO = "SEGUIMIENTO", "En seguimiento"
        CONVERTIDO = "CONVERTIDO", "Convertido en cliente"
        PERDIDO = "PERDIDO", "Perdido"

    ABIERTOS = (Estado.NUEVO, Estado.CONTACTADO, Estado.SEGUIMIENTO)

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="leads")
    nombres = models.CharField("nombres", max_length=150)
    telefono = models.CharField("teléfono", max_length=20, blank=True, default="")
    correo = models.EmailField("correo", blank=True, default="")
    interes = models.CharField("le interesa", max_length=10, choices=Interes.choices, default=Interes.VISA)
    detalle = models.CharField("país, visa o paquete", max_length=150, blank=True, default="",
                               help_text="Ej.: visa de turismo a EE. UU., paquete Cancún 5 días.")
    origen = models.CharField("de dónde llegó", max_length=10, choices=Origen.choices, default=Origen.WHATSAPP)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.NUEVO)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="leads_asignados")
    proximo_contacto = models.DateField("próximo contacto", null=True, blank=True)
    notas = models.TextField(blank=True, default="")
    motivo_perdida = models.CharField("por qué se perdió", max_length=255, blank=True, default="")
    cliente = models.OneToOneField("Cliente", on_delete=models.PROTECT, null=True, blank=True, related_name="lead")
    convertido_en = models.DateTimeField(null=True, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+")

    class Meta:
        verbose_name = "lead"
        verbose_name_plural = "leads"
        ordering = ["-creado_en"]
        indexes = [models.Index(fields=["empresa", "estado"], name="lead_empresa_estado_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(estado__in=["NUEVO", "CONTACTADO", "SEGUIMIENTO", "CONVERTIDO", "PERDIDO"]),
                                   name="lead_estado_valido"),
            models.CheckConstraint(condition=~Q(telefono="") | ~Q(correo=""), name="lead_con_contacto"),
            # Convertido <=> tiene cliente. Perdido => se anota el motivo.
            models.CheckConstraint(condition=Q(estado="CONVERTIDO", cliente__isnull=False, convertido_en__isnull=False)
                                   | (~Q(estado="CONVERTIDO") & Q(cliente__isnull=True)), name="lead_convertido_con_cliente"),
            models.CheckConstraint(condition=~Q(estado="PERDIDO") | ~Q(motivo_perdida=""), name="lead_perdido_con_motivo"),
        ]

    @property
    def abierto(self) -> bool:
        return self.estado in self.ABIERTOS

    @property
    def contacto_vencido(self) -> bool:
        return self.abierto and self.proximo_contacto is not None and self.proximo_contacto < date.today()

    def __str__(self):
        return self.nombres


class SeguimientoLead(models.Model):
    """Cada llamada, mensaje o visita con el lead: el historial no se edita."""

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="seguimientos")
    nota = models.TextField()
    estado = models.CharField("estado tras el contacto", max_length=12, choices=Lead.Estado.choices)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    fecha = models.DateTimeField(db_default=Now())

    class Meta:
        verbose_name = "seguimiento de lead"
        verbose_name_plural = "seguimientos de leads"
        ordering = ["-fecha", "-pk"]

    def __str__(self):
        return f"{self.lead} · {self.fecha:%d/%m/%Y}"


class Cliente(ModeloBase):
    """Cliente de visas o paquetes de una empresa. No es el cliente del call (ventas.Cliente)."""

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="clientes")
    codigo = models.CharField("código", max_length=20)
    linea = models.CharField("línea", max_length=8, choices=Linea.choices, default=Linea.VISAS)
    nombres = models.CharField("nombre / razón social", max_length=150)
    tipo_identificacion = models.CharField("tipo de identificación", max_length=10, choices=TipoIdentificacion.choices,
                                           default=TipoIdentificacion.CEDULA)
    identificacion = models.CharField("identificación", max_length=20)
    telefono = models.CharField("teléfono", max_length=20, blank=True, default="")
    correo = models.EmailField("correo", blank=True, default="")
    direccion = models.CharField("dirección", max_length=255, blank=True, default="")
    observaciones = models.TextField(blank=True, default="")
    activo = models.BooleanField(default=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+")

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["nombres"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "codigo"], name="contab_cliente_codigo_unico"),
            models.UniqueConstraint(fields=["empresa", "identificacion"], name="contab_cliente_identificacion_unica"),
            models.CheckConstraint(condition=Q(linea__in=["VISAS", "PAQUETES", "AMBAS"]), name="contab_cliente_linea_valida"),
        ]

    def __str__(self):
        return f"{self.nombres} ({self.codigo})"


class Proveedor(ModeloBase):
    """Aerolíneas, hoteles, operadores, consulados y proveedores locales."""

    class Tipo(models.TextChoices):
        AEROLINEA = "AEROLINEA", "Aerolínea"
        HOTEL = "HOTEL", "Hotel"
        OPERADOR = "OPERADOR", "Operador turístico"
        CONSULADO = "CONSULADO", "Consulado / embajada"
        LOCAL = "LOCAL", "Proveedor local"
        SERVICIOS = "SERVICIOS", "Servicios profesionales"
        OTRO = "OTRO", "Otro"

    class CondicionPago(models.TextChoices):
        CONTADO = "CONTADO", "De contado"
        CREDITO = "CREDITO", "A crédito"
        ANTICIPADO = "ANTICIPADO", "Pago anticipado"

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="proveedores")
    codigo = models.CharField("código", max_length=20)
    nombre = models.CharField("proveedor", max_length=150)
    tipo_identificacion = models.CharField("tipo de identificación", max_length=10, choices=TipoIdentificacion.choices,
                                           default=TipoIdentificacion.RUC)
    identificacion = models.CharField("RUC / identificación", max_length=20, blank=True, default="")
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.LOCAL)
    contacto = models.CharField("persona de contacto", max_length=150, blank=True, default="")
    telefono = models.CharField("teléfono", max_length=20, blank=True, default="")
    correo = models.EmailField("correo", blank=True, default="")
    condicion_pago = models.CharField("condición de pago", max_length=10, choices=CondicionPago.choices,
                                      default=CondicionPago.CONTADO)
    dias_credito = models.PositiveSmallIntegerField("días de crédito", null=True, blank=True)
    observaciones = models.TextField(blank=True, default="")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "proveedor"
        verbose_name_plural = "proveedores"
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "codigo"], name="proveedor_codigo_unico"),
            models.UniqueConstraint(fields=["empresa", "identificacion"], condition=~Q(identificacion=""),
                                    name="proveedor_identificacion_unica"),
            models.CheckConstraint(condition=~Q(condicion_pago="CREDITO") | Q(dias_credito__gt=0),
                                   name="proveedor_credito_con_dias"),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.codigo})"


# --------------------------------------------------------------------------- caja y bancos
class CuentaFinanciera(ModeloBase):
    """Una caja o una cuenta bancaria de la empresa, ligada a su cuenta del plan (1.1.01.01, 1.1.02.01…)."""

    class Tipo(models.TextChoices):
        CAJA = "CAJA", "Caja"
        CORRIENTE = "CORRIENTE", "Banco · cuenta corriente"
        AHORROS = "AHORROS", "Banco · cuenta de ahorros"

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="cuentas_financieras")
    nombre = models.CharField(max_length=80, help_text="Ej.: Caja general, Pichincha corriente.")
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.CAJA)
    banco = models.CharField(max_length=80, blank=True, default="")
    numero = models.CharField("número de cuenta", max_length=30, blank=True, default="")
    cuenta_contable = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, related_name="cuentas_financieras",
                                        verbose_name="cuenta del plan")
    saldo_inicial = models.DecimalField("saldo inicial", **DINERO, default=CERO)
    fecha_saldo_inicial = models.DateField("saldo a la fecha")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "caja o banco"
        verbose_name_plural = "cajas y bancos"
        ordering = ["empresa", "tipo", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "nombre"], name="cuentafin_nombre_unico"),
            models.CheckConstraint(condition=Q(tipo="CAJA") | ~Q(banco=""), name="cuentafin_banco_obligatorio"),
            models.CheckConstraint(condition=~Q(tipo="CAJA") | Q(saldo_inicial__gte=0), name="cuentafin_caja_no_negativa"),
        ]

    @property
    def es_caja(self) -> bool:
        return self.tipo == self.Tipo.CAJA

    def __str__(self):
        return self.nombre


class MovimientoFinanciero(ModeloBase):
    """
    Un ingreso, depósito, transferencia o egreso de una caja o banco (hoja CAJA_BANCOS).
    No se edita ni se borra: se anula con motivo. Lo único que cambia después es la conciliación.
    """

    class Tipo(models.TextChoices):
        INGRESO = "INGRESO", "Ingreso en efectivo"
        DEPOSITO = "DEPOSITO", "Depósito"
        TRANSFERENCIA_RECIBIDA = "TRANSF_REC", "Transferencia recibida"
        EGRESO = "EGRESO", "Egreso / pago"
        TRANSFERENCIA_ENVIADA = "TRANSF_ENV", "Transferencia enviada"
        COMISION_BANCARIA = "COMISION", "Comisión o cargo bancario"
        TRASPASO_ENTRADA = "TRASP_ENT", "Traspaso recibido de otra cuenta"
        TRASPASO_SALIDA = "TRASP_SAL", "Traspaso a otra cuenta"

    ENTRADAS = ("INGRESO", "DEPOSITO", "TRANSF_REC", "TRASP_ENT")
    SALIDAS = ("EGRESO", "TRANSF_ENV", "COMISION", "TRASP_SAL")
    TRASPASOS = ("TRASP_ENT", "TRASP_SAL")

    cuenta = models.ForeignKey(CuentaFinanciera, on_delete=models.PROTECT, related_name="movimientos")
    fecha = models.DateField()
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    concepto = models.CharField(max_length=200)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, null=True, blank=True, related_name="movimientos")
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, null=True, blank=True, related_name="movimientos")
    tercero = models.CharField("otro tercero", max_length=150, blank=True, default="",
                               help_text="Si no es un cliente ni un proveedor registrado.")
    referencia = models.CharField("referencia", max_length=60, blank=True, default="",
                                  help_text="N.º de comprobante, de depósito o de transferencia.")
    valor = models.DecimalField(**DINERO)
    # Traspaso entre cuentas propias: se guarda primero la entrada y la salida apunta a ella.
    traspaso = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="traspaso_origen",
                                    help_text="La entrada en la otra cuenta (solo en la salida de un traspaso).")
    conciliado = models.BooleanField(default=False)
    conciliado_en = models.DateField("conciliado el", null=True, blank=True)
    soporte = models.FileField(upload_to=ruta_soporte, max_length=255, blank=True, default="")
    soporte_mime = models.CharField(max_length=50, blank=True, default="")
    observaciones = models.CharField(max_length=255, blank=True, default="")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+")
    anulado = models.BooleanField(default=False)
    motivo_anulacion = models.CharField(max_length=255, blank=True, default="")
    anulado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    anulado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "movimiento de caja o banco"
        verbose_name_plural = "movimientos de caja y bancos"
        ordering = ["fecha", "pk"]
        indexes = [models.Index(fields=["cuenta", "fecha"], name="movfin_cuenta_fecha_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(valor__gt=0), name="movfin_valor_positivo"),
            models.CheckConstraint(condition=Q(tipo__in=["INGRESO", "DEPOSITO", "TRANSF_REC", "TRASP_ENT",
                                                         "EGRESO", "TRANSF_ENV", "COMISION", "TRASP_SAL"]),
                                   name="movfin_tipo_valido"),
            models.CheckConstraint(condition=Q(cliente__isnull=True) | Q(proveedor__isnull=True),
                                   name="movfin_un_solo_tercero"),
            models.CheckConstraint(condition=Q(tipo="TRASP_SAL", traspaso__isnull=False)
                                   | (~Q(tipo="TRASP_SAL") & Q(traspaso__isnull=True)),
                                   name="movfin_traspaso_con_par"),
            models.CheckConstraint(condition=Q(conciliado=False) | Q(conciliado_en__isnull=False),
                                   name="movfin_conciliado_con_fecha"),
            models.CheckConstraint(condition=Q(anulado=False) | (~Q(motivo_anulacion="") & Q(anulado_en__isnull=False)),
                                   name="movfin_anulado_con_motivo"),
            models.CheckConstraint(condition=Q(anulado=False) | Q(conciliado=False), name="movfin_anulado_no_conciliado"),
        ]

    @property
    def entra(self) -> bool:
        return self.tipo in self.ENTRADAS

    @property
    def es_traspaso(self) -> bool:
        return self.tipo in self.TRASPASOS

    @property
    def contraparte(self):
        """El otro lado del traspaso (la entrada o la salida en la otra cuenta)."""
        if self.tipo == self.Tipo.TRASPASO_SALIDA:
            return self.traspaso
        if self.tipo == self.Tipo.TRASPASO_ENTRADA:
            return getattr(self, "traspaso_origen", None)
        return None

    @property
    def tercero_visible(self) -> str:
        return str(self.cliente or self.proveedor or self.tercero or "")

    def __str__(self):
        return f"{self.get_tipo_display()} {self.valor} · {self.fecha:%d/%m/%Y}"


# --------------------------------------------------------------------------- libro diario
class Asiento(ModeloBase):
    """
    Asiento del libro diario, registrado a mano. Mientras es borrador se puede corregir o borrar; al contabilizarlo
    recibe su número (correlativo por empresa), tiene que cuadrar y ya no cambia: si estaba mal, se anula con motivo.
    Los saldos iniciales son el asiento de apertura (uno por empresa).
    """

    class Tipo(models.TextChoices):
        APERTURA = "APERTURA", "Apertura (saldos iniciales)"
        DIARIO = "DIARIO", "Diario"
        AJUSTE = "AJUSTE", "Ajuste"
        CIERRE = "CIERRE", "Cierre"

    class Estado(models.TextChoices):
        BORRADOR = "BORRADOR", "Borrador"
        CONTABILIZADO = "CONTABILIZADO", "Contabilizado"
        ANULADO = "ANULADO", "Anulado"

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="asientos")
    numero = models.PositiveIntegerField("asiento N.º", null=True, blank=True, editable=False)
    fecha = models.DateField()
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.DIARIO)
    concepto = models.CharField(max_length=255)
    documento = models.CharField("documento soporte", max_length=80, blank=True, default="",
                                 help_text="Factura, comprobante, estado de cuenta… (el número o la referencia).")
    estado = models.CharField(max_length=13, choices=Estado.choices, default=Estado.BORRADOR, editable=False)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+")
    contabilizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name="+")
    contabilizado_en = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.CharField(max_length=255, blank=True, default="")
    anulado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    anulado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "asiento"
        verbose_name_plural = "libro diario"
        ordering = ["fecha", "numero", "pk"]
        indexes = [models.Index(fields=["empresa", "fecha"], name="asiento_empresa_fecha_idx")]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "numero"], condition=Q(numero__isnull=False),
                                    name="asiento_numero_unico"),
            # Una sola apertura vigente por empresa (la anulada no cuenta).
            models.UniqueConstraint(fields=["empresa"], condition=Q(tipo="APERTURA") & ~Q(estado="ANULADO"),
                                    name="asiento_una_apertura"),
            models.CheckConstraint(condition=Q(tipo__in=["APERTURA", "DIARIO", "AJUSTE", "CIERRE"]), name="asiento_tipo_valido"),
            models.CheckConstraint(condition=Q(estado__in=["BORRADOR", "CONTABILIZADO", "ANULADO"]), name="asiento_estado_valido"),
            models.CheckConstraint(condition=Q(estado="BORRADOR") | (Q(numero__isnull=False) & Q(contabilizado_en__isnull=False)),
                                   name="asiento_contabilizado_numerado"),
            models.CheckConstraint(condition=~Q(estado="ANULADO") | (~Q(motivo_anulacion="") & Q(anulado_en__isnull=False)),
                                   name="asiento_anulado_con_motivo"),
        ]

    @property
    def periodo(self) -> str:
        return f"{self.fecha:%Y-%m}"

    @property
    def es_borrador(self) -> bool:
        return self.estado == self.Estado.BORRADOR

    def __str__(self):
        numero = f"N.º {self.numero}" if self.numero else "borrador"
        return f"Asiento {numero} · {self.fecha:%d/%m/%Y}"


class LineaAsiento(models.Model):
    """Una fila del asiento: una cuenta con un valor al debe o al haber (nunca a los dos)."""

    asiento = models.ForeignKey(Asiento, on_delete=models.CASCADE, related_name="lineas")
    orden = models.PositiveSmallIntegerField(default=0)
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, related_name="lineas")
    detalle = models.CharField(max_length=255, blank=True, default="")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, null=True, blank=True, related_name="lineas_asiento")
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, null=True, blank=True, related_name="lineas_asiento")
    tercero = models.CharField("otro tercero", max_length=150, blank=True, default="")
    debe = models.DecimalField(**DINERO, default=CERO)
    haber = models.DecimalField(**DINERO, default=CERO)

    class Meta:
        verbose_name = "línea de asiento"
        verbose_name_plural = "líneas de asiento"
        ordering = ["asiento", "orden", "pk"]
        constraints = [
            models.CheckConstraint(condition=(Q(debe__gt=0) & Q(haber=0)) | (Q(haber__gt=0) & Q(debe=0)),
                                   name="linea_debe_o_haber"),
            models.CheckConstraint(condition=Q(cliente__isnull=True) | Q(proveedor__isnull=True),
                                   name="linea_un_solo_tercero"),
        ]

    @property
    def tercero_visible(self) -> str:
        return str(self.cliente or self.proveedor or self.tercero or "")

    def __str__(self):
        return f"{self.cuenta.codigo} D {self.debe} H {self.haber}"


# --------------------------------------------------------------------------- activos fijos
class ActivoFijo(ModeloBase):
    """Muebles, equipos y vehículos (hojas INVENTARIO y ACTIVOS_FIJOS). La depreciación es en línea recta."""

    class Estado(models.TextChoices):
        EN_USO = "EN_USO", "En uso"
        REPARACION = "REPARACION", "En reparación"
        BODEGA = "BODEGA", "En bodega"
        BAJA = "BAJA", "Dado de baja"

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="activos")
    codigo = models.CharField("código", max_length=20)
    descripcion = models.CharField("descripción", max_length=150)
    cuenta_contable = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, null=True, blank=True,
                                        related_name="activos", verbose_name="cuenta del plan",
                                        help_text="Muebles y enseres, equipos de computación, de oficina o vehículos.")
    cantidad = models.PositiveIntegerField(default=1)
    valor_unitario = models.DecimalField("valor unitario", **DINERO, default=CERO)
    fecha_compra = models.DateField("fecha de compra", null=True, blank=True)
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, null=True, blank=True, related_name="activos")
    factura = models.CharField(max_length=60, blank=True, default="")
    ubicacion = models.CharField("ubicación", max_length=100, blank=True, default="")
    custodio = models.CharField(max_length=150, blank=True, default="")
    vida_util_anios = models.PositiveSmallIntegerField("vida útil (años)", null=True, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.EN_USO)
    observaciones = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "activo fijo"
        verbose_name_plural = "activos fijos"
        ordering = ["codigo"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "codigo"], name="activo_codigo_unico"),
            models.CheckConstraint(condition=Q(cantidad__gt=0), name="activo_cantidad_positiva"),
            models.CheckConstraint(condition=Q(valor_unitario__gte=0), name="activo_valor_no_negativo"),
            models.CheckConstraint(condition=Q(vida_util_anios__isnull=True) | Q(vida_util_anios__gt=0),
                                   name="activo_vida_util_positiva"),
        ]

    @property
    def costo(self) -> Decimal:
        return (self.valor_unitario * self.cantidad).quantize(Decimal("0.01"))

    def depreciacion_acumulada(self, al: date | None = None) -> Decimal | None:
        """Línea recta por meses completos desde la compra. None si falta la fecha o la vida útil."""
        if not (self.fecha_compra and self.vida_util_anios):
            return None
        al = al or date.today()
        meses = max(0, (al.year - self.fecha_compra.year) * 12 + al.month - self.fecha_compra.month)
        meses = min(meses, self.vida_util_anios * 12)
        return (self.costo * meses / (self.vida_util_anios * 12)).quantize(Decimal("0.01"))

    def valor_libros(self, al: date | None = None) -> Decimal | None:
        depreciacion = self.depreciacion_acumulada(al)
        return None if depreciacion is None else self.costo - depreciacion

    def __str__(self):
        return f"{self.codigo} {self.descripcion}"
