"""
Reglas de pago de nómina que garantiza la base de datos (migración nomina/0002): periodos,
novedades, liquidación y solicitud de pago con factura.

Estas tablas todavía no tienen pantalla (SCRUM-190); las pruebas comprueban que las reglas
ya protegen los datos para cuando se construya el cierre de mes.

    python manage.py test apps.nomina.tests_pagos
"""
from datetime import date
from decimal import Decimal as D

from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Rol, Usuario
from apps.documentos.models import Documento, TipoDocumento
from apps.personal.models import Cargo, Empleado, HistorialSalario

from . import calculo
from .models import LiquidacionNomina, NovedadNomina, PeriodoNomina, SolicitudPago, TipoNovedadNomina


class BasePagos(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin.pagos", None, "clave-segura-123", rol=Rol.ADMINISTRADOR)
        cargo = Cargo.objects.get(nombre="Asesor de ventas")
        cls.ana = Empleado.objects.create(identificacion="1710034065", nombres="Ana", apellidos="Vera", cargo=cargo,
                                          fecha_ingreso=date(2026, 1, 1))
        cls.luis = Empleado.objects.create(identificacion="0926687856", nombres="Luis", apellidos="Paz", cargo=cargo,
                                           fecha_ingreso=date(2026, 1, 1))
        HistorialSalario.objects.create(empleado=cls.ana, salario_base=D("400.00"), vigente_desde=date(2026, 1, 1))
        cls.anticipo = TipoNovedadNomina.objects.get(codigo="ANTICIPO")

    def setUp(self):
        self.periodo = PeriodoNomina.objects.create(nombre="Noviembre 2026", fecha_inicio=date(2026, 11, 1), fecha_fin=date(2026, 11, 30))

    def rechaza(self, accion):
        with self.assertRaises(DatabaseError), transaction.atomic():
            accion()

    def estado_periodo(self, estado):
        ahora = timezone.now()
        extra = {"aprobado_en": ahora} if estado != "ABIERTO" else {}
        if estado == "CERRADO":
            extra["cerrado_en"] = ahora
        PeriodoNomina.objects.filter(pk=self.periodo.pk).update(estado=estado, **extra)

    def liquidacion(self, empleado=None, ingresos="400.00", deducciones="64.33", estado="APROBADA"):
        ingresos, deducciones = D(ingresos), D(deducciones)
        return LiquidacionNomina.objects.create(
            periodo=self.periodo, empleado=empleado or self.ana, salario_base=ingresos, total_ingresos=ingresos,
            total_deducciones=deducciones, neto=ingresos - deducciones, estado=estado, calculada_en=timezone.now())

    def factura(self, empleado=None, n=1, eliminado=False):
        return Documento.objects.create(
            empleado=empleado or self.ana, tipo=TipoDocumento.objects.get(codigo="FACTURA"), archivo=f"factura{n}.pdf",
            nombre_original=f"factura{n}.pdf", tipo_mime="application/pdf", tamano_bytes=10, hash_sha256=f"{n:064x}",
            eliminado=eliminado)


class PeriodosTests(BasePagos):
    def test_dos_periodos_no_se_solapan(self):
        self.rechaza(lambda: PeriodoNomina.objects.create(nombre="Quincena", fecha_inicio=date(2026, 11, 16), fecha_fin=date(2026, 12, 15)))
        PeriodoNomina.objects.create(nombre="Diciembre 2026", fecha_inicio=date(2026, 12, 1), fecha_fin=date(2026, 12, 31))

    def test_fechas_del_periodo(self):
        self.rechaza(lambda: PeriodoNomina.objects.create(nombre="Al revés", fecha_inicio=date(2027, 1, 31), fecha_fin=date(2027, 1, 1)))
        self.rechaza(lambda: PeriodoNomina.objects.create(nombre="Pago antes", fecha_inicio=date(2027, 1, 1), fecha_fin=date(2027, 1, 31),
                                                          fecha_pago=date(2026, 12, 31)))

    def test_aprobar_y_cerrar_exigen_fecha(self):
        self.rechaza(lambda: PeriodoNomina.objects.filter(pk=self.periodo.pk).update(estado="APROBADO"))
        self.rechaza(lambda: PeriodoNomina.objects.filter(pk=self.periodo.pk).update(estado="CERRADO", aprobado_en=timezone.now()))

    def test_periodo_aprobado_no_admite_cambios_en_sus_novedades(self):
        novedad = NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D("50"))
        self.estado_periodo("APROBADO")
        self.rechaza(lambda: NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D("10")))
        self.rechaza(lambda: NovedadNomina.objects.filter(pk=novedad.pk).update(valor=D("500")))
        self.rechaza(lambda: NovedadNomina.objects.filter(pk=novedad.pk).delete())
        # La liquidación de un periodo aprobado sí puede pasar a pagada.
        liq = self.liquidacion()
        LiquidacionNomina.objects.filter(pk=liq.pk).update(estado="PAGADA")

    def test_periodo_cerrado_queda_congelado(self):
        liq = self.liquidacion()
        self.estado_periodo("CERRADO")
        self.rechaza(lambda: PeriodoNomina.objects.filter(pk=self.periodo.pk).update(nombre="Otro"))
        self.rechaza(lambda: PeriodoNomina.objects.filter(pk=self.periodo.pk).update(estado="ABIERTO"))
        self.rechaza(lambda: PeriodoNomina.objects.filter(pk=self.periodo.pk).delete())
        self.rechaza(lambda: LiquidacionNomina.objects.filter(pk=liq.pk).update(neto=D("1"), total_ingresos=D("1"), total_deducciones=D("0")))
        self.rechaza(lambda: self.liquidacion(empleado=self.luis))
        self.rechaza(lambda: NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D("10")))


class NovedadesTests(BasePagos):
    def test_valor_y_cantidad_positivos(self):
        self.rechaza(lambda: NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D("0")))
        self.rechaza(lambda: NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D("10"), cantidad=0))

    def test_aprobar_exige_fecha(self):
        self.rechaza(lambda: NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D("10"),
                                                          estado="APROBADA"))

    def test_solo_el_anticipo_aprobado_se_descuenta_del_rol(self):
        """Sueldo 400: aprobado 60 se descuenta; pendiente 999, rechazado 80 y anulado 70 no."""
        NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D("60"), estado="APROBADA",
                                     aprobada_en=timezone.now())
        for estado, valor in (("PENDIENTE", "999"), ("RECHAZADA", "80"), ("ANULADA", "70")):
            NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D(valor), estado=estado)
        e = calculo.estimar_mes(self.ana, 2026, 11)
        self.assertEqual((e.total_anticipos, e.neto), (D("60.00"), D("340.00")))
        self.assertEqual(calculo.estimar_mes(self.ana, 2026, 12).total_anticipos, D("0.00"))  # no pasa a otro mes

    def test_dos_anticipos_aprobados_se_suman(self):
        for valor in ("60", "25.50"):
            NovedadNomina.objects.create(empleado=self.ana, periodo=self.periodo, tipo=self.anticipo, valor=D(valor), estado="APROBADA",
                                         aprobada_en=timezone.now())
        e = calculo.estimar_mes(self.ana, 2026, 11)
        self.assertEqual((e.total_anticipos, e.neto), (D("85.50"), D("314.50")))


class LiquidacionTests(BasePagos):
    def test_el_neto_siempre_cuadra(self):
        liq = self.liquidacion(ingresos="400.00", deducciones="64.33")
        self.assertEqual(liq.neto, D("335.67"))
        self.rechaza(lambda: LiquidacionNomina.objects.filter(pk=liq.pk).update(neto=D("400.00")))
        self.rechaza(lambda: LiquidacionNomina.objects.create(periodo=self.periodo, empleado=self.luis, total_ingresos=D("400"),
                                                              total_deducciones=D("50"), neto=D("360")))

    def test_sin_valores_ni_neto_negativos(self):
        self.rechaza(lambda: self.liquidacion(ingresos="400.00", deducciones="450.00"))  # neto −50
        self.rechaza(lambda: LiquidacionNomina.objects.create(periodo=self.periodo, empleado=self.luis, total_ingresos=D("-1"),
                                                              total_deducciones=D("-1"), neto=D("0")))

    def test_una_liquidacion_por_empleado_y_periodo(self):
        self.liquidacion()
        self.rechaza(lambda: self.liquidacion())
        self.liquidacion(empleado=self.luis)


class SolicitudPagoTests(BasePagos):
    def test_pago_con_factura_del_mismo_empleado_hasta_el_neto(self):
        liq = self.liquidacion()  # neto 335,67
        solicitud = SolicitudPago.objects.create(liquidacion=liq, factura=self.factura(), monto=D("335.67"), solicitada_por=self.admin)
        self.assertEqual(solicitud.estado, "SOLICITADA")

    def test_no_se_paga_mas_que_el_neto(self):
        liq = self.liquidacion()
        self.rechaza(lambda: SolicitudPago.objects.create(liquidacion=liq, factura=self.factura(), monto=D("335.68")))
        self.rechaza(lambda: SolicitudPago.objects.create(liquidacion=liq, factura=self.factura(), monto=D("0")))

    def test_la_factura_es_obligatoria_vigente_y_del_mismo_empleado(self):
        liq = self.liquidacion()
        self.rechaza(lambda: SolicitudPago.objects.create(liquidacion=liq, monto=D("100")))  # sin factura
        self.rechaza(lambda: SolicitudPago.objects.create(liquidacion=liq, factura=self.factura(self.luis, n=2), monto=D("100")))
        self.rechaza(lambda: SolicitudPago.objects.create(liquidacion=liq, factura=self.factura(n=3, eliminado=True), monto=D("100")))

    def test_solo_liquidaciones_aprobadas(self):
        for n, estado in enumerate(("BORRADOR", "CALCULADA", "ANULADA"), start=1):
            with self.subTest(estado=estado):
                LiquidacionNomina.objects.filter(periodo=self.periodo).delete()
                liq = self.liquidacion(estado=estado)
                self.rechaza(lambda: SolicitudPago.objects.create(liquidacion=liq, factura=self.factura(n=n), monto=D("100")))

    def test_una_solicitud_por_liquidacion(self):
        liq = self.liquidacion()
        SolicitudPago.objects.create(liquidacion=liq, factura=self.factura(), monto=D("100"))
        self.rechaza(lambda: SolicitudPago.objects.create(liquidacion=liq, factura=self.factura(n=2), monto=D("100")))

    def test_revisar_y_pagar_exigen_fecha(self):
        solicitud = SolicitudPago.objects.create(liquidacion=self.liquidacion(), factura=self.factura(), monto=D("335.67"))
        self.rechaza(lambda: SolicitudPago.objects.filter(pk=solicitud.pk).update(estado="APROBADA"))
        self.rechaza(lambda: SolicitudPago.objects.filter(pk=solicitud.pk).update(estado="PAGADA", revisada_en=timezone.now()))
        ahora = timezone.now()
        SolicitudPago.objects.filter(pk=solicitud.pk).update(estado="PAGADA", revisada_en=ahora, pagada_en=ahora, referencia_pago="TRX-001")
        self.rechaza(lambda: SolicitudPago.objects.filter(pk=solicitud.pk).update(monto=D("999")))  # nunca por más del neto, tampoco después de pagada
