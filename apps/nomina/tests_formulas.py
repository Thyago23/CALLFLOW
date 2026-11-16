"""
Verificación de las fórmulas de nómina y caja chica con valores calculados a mano (16/11/2026).

Complementa tests_calculo.py y tests_reportes.py con casos límite: bordes de tramos, tope del 10 %,
redondeo, sueldos distintos de 400, rol completo (descuentos + anticipo + comisión), ingreso a mitad
de mes, cambio de versión de reglas y cuadre de caja chica entre meses.

    python manage.py test apps.nomina.tests_formulas
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.asistencia.models import NovedadAsistencia
from apps.cierre.models import GestionCierre
from apps.core.models import Rol, Usuario
from apps.documentos.models import Documento, TipoDocumento
from apps.personal.models import AsignacionComision, Cargo, Empleado, EsquemaComision, HistorialSalario
from apps.ventas.models import Cliente, FormularioDAMI

from . import caja_chica as caja
from . import calculo
from .models import MovimientoCajaChica, NovedadNomina, PeriodoNomina, TipoNovedadNomina

INGRESO, EGRESO = MovimientoCajaChica.Tipo.INGRESO, MovimientoCajaChica.Tipo.EGRESO
NOV, OCT = (2026, 11), (2026, 10)


class ComisionBordesTests(TestCase):
    """El porcentaje del tramo alcanzado se aplica a TODO el total; 'hasta' es inclusive."""

    def test_centavos_sobre_el_borde(self):
        visado = EsquemaComision.objects.get(nombre="Visado")
        paqueteria = EsquemaComision.objects.get(nombre="Paquetería")
        casos = [
            (visado, "1300.00", "0", "0.00"),
            (visado, "1300.01", "11", "143.00"),   # 1300,01 × 11 % = 143,0011
            (visado, "3000.01", "15", "450.00"),   # 3000,01 × 15 % = 450,0015
            (visado, "2727.27", "11", "300.00"),   # 300,0000 (redondeo exacto)
            (visado, "1999.95", "11", "219.99"),   # 219,9945 → 219,99
            (visado, "1999.96", "11", "220.00"),   # 219,9956 → 220,00
            (paqueteria, "3000.01", "11", "330.00"),
            (paqueteria, "5000.00", "11", "550.00"),
            (paqueteria, "5000.01", "15", "750.00"),
            (visado, "-50", "0", "0.00"),           # nunca comisión negativa
        ]
        for esquema, total, pct, monto in casos:
            with self.subTest(esquema=esquema.nombre, total=total):
                self.assertEqual(calculo.comision(esquema, D(total)), (D(pct), D(monto)))

    def test_salto_de_tramo(self):
        """Comportamiento de la regla «se aplica a todo el total»: vender 1 dólar más sube 143 de comisión."""
        visado = EsquemaComision.objects.get(nombre="Visado")
        self.assertEqual(calculo.comision(visado, D("1300"))[1], D("0.00"))
        self.assertEqual(calculo.comision(visado, D("1301"))[1], D("143.11"))
        self.assertEqual(calculo.comision(visado, D("3000"))[1], D("330.00"))
        self.assertEqual(calculo.comision(visado, D("3001"))[1], D("450.15"))  # +120,15 por 1 dólar


class BaseRol(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin.formulas", None, "clave-segura-123", rol=Rol.ADMINISTRADOR)
        cls.encargado = Empleado.objects.create(identificacion="0926687856", nombres="Luis", apellidos="Paz",
                                                cargo=Cargo.objects.get(nombre="Supervisor"), fecha_ingreso=date(2026, 1, 1))
        cls.emp = cls.crear_vendedor("1710034065", "400.00")

    @classmethod
    def crear_vendedor(cls, cedula, sueldo, ingreso=date(2026, 1, 1)):
        emp = Empleado.objects.create(identificacion=cedula, nombres="Ana", apellidos=f"Vera {cedula[-3:]}",
                                      cargo=Cargo.objects.get(nombre="Asesor de ventas"), fecha_ingreso=ingreso)
        HistorialSalario.objects.create(empleado=emp, salario_base=D(sueldo), vigente_desde=ingreso)
        AsignacionComision.objects.create(empleado=emp, esquema=EsquemaComision.objects.get(nombre="Visado"), vigente_desde=ingreso)
        return emp

    def novedad(self, emp, fecha, tipo, estado="NO_JUSTIFICADA", minutos=None):
        extra = {} if estado == "PENDIENTE" else {"revisada_en": timezone.now(), "revisada_por": self.admin}
        if estado == "JUSTIFICADA":
            extra["soporte"] = Documento.objects.create(
                empleado=emp, tipo=TipoDocumento.objects.get(codigo="SOPORTE_ASISTENCIA"), archivo=f"s{emp.pk}-{fecha}.pdf",
                nombre_original="s.pdf", tipo_mime="application/pdf", tamano_bytes=10,
                hash_sha256=f"{emp.pk:08d}{fecha:%Y%m%d}".ljust(64, "0"),
            )
        return NovedadAsistencia.objects.create(empleado=emp, fecha=fecha, tipo=tipo, estado=estado, minutos=minutos, **extra)

    def venta(self, emp, valor, n):
        cliente = Cliente.objects.create(identificacion=f"C-{emp.pk}-{n}", nombres="Cliente", apellidos=str(n), telefono="0999999999")
        dami = FormularioDAMI.objects.create(vendedor=emp, cliente=cliente, estado="ENVIADO", enviado_en=timezone.now())
        GestionCierre.objects.create(formulario=dami, encargado=self.encargado, resultado="VENTA_CERRADA", valor_venta=D(valor))

    def anticipo(self, emp, valor, anio, mes):
        inicio = date(anio, mes, 1)
        periodo, _ = PeriodoNomina.objects.get_or_create(
            fecha_inicio=inicio, defaults={"nombre": f"{anio}-{mes}", "fecha_fin": calculo.ultimo_dia(anio, mes)})
        NovedadNomina.objects.create(empleado=emp, periodo=periodo, tipo=TipoNovedadNomina.objects.get(codigo="ANTICIPO"),
                                     valor=D(valor), estado="APROBADA", aprobada_en=timezone.now())


class PenalizacionesV2Tests(BaseRol):
    """Noviembre de 2026 en adelante: 1–9 min $1 · 10–29 min $3 · 30+ min $4 + falta · tope 10 % del sueldo."""

    def estimar(self, emp=None):
        return calculo.estimar_mes(emp or self.emp, *NOV)

    def test_una_falta_no_justificada_es_el_10_por_ciento(self):
        self.novedad(self.emp, date(2026, 11, 3), "AUSENCIA")
        e = self.estimar()
        self.assertEqual((e.valor_faltas_injustificadas, e.ajuste_tope, e.total_descuentos, e.neto),
                         (D("40.00"), D("0.00"), D("40.00"), D("360.00")))
        self.assertEqual(e.dias_trabajados, 29)

    def test_dos_faltas_no_justificadas_quedan_en_el_tope(self):
        """2 × 40 = 80, el tope es 40: la segunda falta no se descuenta (consecuencia de la regla aprobada)."""
        self.novedad(self.emp, date(2026, 11, 3), "AUSENCIA")
        self.novedad(self.emp, date(2026, 11, 4), "AUSENCIA")
        e = self.estimar()
        self.assertEqual((e.valor_faltas_injustificadas, e.tope_penalizaciones, e.ajuste_tope, e.total_descuentos, e.neto),
                         (D("80.00"), D("40.00"), D("40.00"), D("40.00"), D("360.00")))
        self.assertEqual(e.dias_trabajados, 28)

    def test_atrasos_por_tramo_y_bordes(self):
        for dia, minutos in ((3, 1), (4, 9), (5, 10), (6, 29)):
            self.novedad(self.emp, date(2026, 11, dia), "ATRASO", minutos=minutos)
        e = self.estimar()
        self.assertEqual([d.valor for d in e.atrasos], [D("1.00"), D("1.00"), D("3.00"), D("3.00")])
        self.assertEqual((e.valor_atrasos, e.total_descuentos, e.neto, e.llamados_mes), (D("8.00"), D("8.00"), D("392.00"), []))

    def test_tope_con_sueldo_distinto_de_400(self):
        """Sueldo 460: falta = 46, tope = 46. Falta 46 + atraso 3 = 49 → se descuentan 46."""
        emp = self.crear_vendedor("0912345675", "460.00")
        self.novedad(emp, date(2026, 11, 3), "AUSENCIA")
        self.novedad(emp, date(2026, 11, 4), "ATRASO", minutos=15)
        e = self.estimar(emp)
        self.assertEqual((e.tope_penalizaciones, e.ajuste_tope, e.total_descuentos, e.neto),
                         (D("46.00"), D("3.00"), D("46.00"), D("414.00")))

    def test_redondeo_de_la_falta(self):
        """433,33 × 10 % = 43,333 → 43,33."""
        emp = self.crear_vendedor("1722222224", "433.33")
        self.novedad(emp, date(2026, 11, 3), "AUSENCIA")
        e = self.estimar(emp)
        self.assertEqual((e.valor_faltas_injustificadas, e.neto), (D("43.33"), D("390.00")))

    def test_falta_justificada_es_monto_fijo_aunque_cambie_el_sueldo(self):
        """La regla fija 13,33 (un día de 400/30). Con sueldo 600 un día vale 20,00, pero se descuenta 13,33."""
        emp = self.crear_vendedor("1733333336", "600.00")
        self.novedad(emp, date(2026, 11, 3), "AUSENCIA", "JUSTIFICADA")
        e = self.estimar(emp)
        self.assertEqual((e.valor_faltas_justificadas, e.ajuste_tope, e.neto), (D("13.33"), D("0.00"), D("586.67")))
        self.assertEqual(e.dias_trabajados, 30)  # la justificada cuenta como trabajada

    def test_llamado_pendiente_se_cobra_y_se_marca(self):
        self.novedad(self.emp, date(2026, 11, 3), "ATRASO", "PENDIENTE", minutos=30)
        e = self.estimar()
        self.assertEqual([(d.clase, d.valor, d.pendiente) for d in e.descuentos],
                         [("atraso", D("4.00"), True), ("falta_injustificada", D("40.00"), True)])
        self.assertEqual((e.pendientes, e.ajuste_tope, e.neto), (2, D("4.00"), D("360.00")))

    def test_atraso_justificado_no_cuenta(self):
        self.novedad(self.emp, date(2026, 11, 3), "ATRASO", "JUSTIFICADA", minutos=45)
        e = self.estimar()
        self.assertEqual((e.descuentos, e.llamados_mes, e.total_atrasos, e.neto), ([], [], 0, D("400.00")))

    def test_rol_completo(self):
        """
        Sueldo 400 · ventas 2.000 (Visado 11 %) → comisión 220
        falta justificada 13,33 + atraso 5 min 1,00 = 14,33 de descuentos · anticipo 50
        neto = 400 − 14,33 − 50 = 335,67 · total a pagar = 335,67 + 220 = 555,67
        """
        self.venta(self.emp, "1200.00", 1)
        self.venta(self.emp, "800.00", 2)
        self.novedad(self.emp, date(2026, 11, 3), "AUSENCIA", "JUSTIFICADA")
        self.novedad(self.emp, date(2026, 11, 4), "ATRASO", minutos=5)
        self.anticipo(self.emp, "50", *NOV)
        e = self.estimar()
        self.assertEqual((e.total_ventas, e.porcentaje_comision, e.comision), (D("2000.00"), D("11"), D("220.00")))
        self.assertEqual((e.total_descuentos, e.total_anticipos, e.total_descuentos_y_anticipos), (D("14.33"), D("50.00"), D("64.33")))
        self.assertEqual((e.total_ingresos, e.neto, e.total_a_pagar), (D("400.00"), D("335.67"), D("555.67")))

    def test_resumen_de_nomina_suma_los_roles(self):
        otro = self.crear_vendedor("0912345675", "460.00")
        self.novedad(self.emp, date(2026, 11, 3), "AUSENCIA")       # −40
        self.novedad(otro, date(2026, 11, 4), "ATRASO", minutos=12)  # −3
        self.venta(otro, "1500.00", 1)                              # 165 de comisión
        r = calculo.nomina_del_mes(*NOV)
        mios = [x for x in r.roles if x.empleado in (self.emp, otro)]
        self.assertEqual(sum((x.neto for x in mios), D("0")), D("817.00"))  # 360 + 457
        self.assertEqual(r.total_neto, calculo._dinero(sum((x.neto for x in r.roles), D("0"))))
        self.assertEqual(r.total_a_pagar, r.total_neto + r.total_comisiones)

    def test_anticipo_mayor_que_el_sueldo_deja_neto_negativo(self):
        """El cálculo no lo impide: hoy el rol mostraría un neto negativo (la tabla de liquidación sí lo rechaza)."""
        self.anticipo(self.emp, "450", *NOV)
        self.assertEqual(self.estimar().neto, D("-50.00"))

    def test_ingreso_a_mitad_de_mes_cobra_el_sueldo_completo(self):
        """
        HALLAZGO: los días trabajados se calculan (15), pero el neto usa el sueldo completo (400).
        Si la empresa paga proporcional, debería ser 400 × 15 / 30 = 200.
        """
        emp = self.crear_vendedor("1744444448", "400.00", ingreso=date(2026, 11, 16))
        e = self.estimar(emp)
        self.assertEqual(e.dias_trabajados, 15)
        self.assertEqual(e.neto, D("400.00"))


class PenalizacionesV1Tests(BaseRol):
    """Octubre de 2026 (v1): 1 min $1 · 2–10 min $5 · más de 10 min llamado sin multa · sin tope."""

    def test_octubre_usa_la_regla_v1(self):
        for dia, minutos in ((1, 1), (2, 2), (5, 10), (6, 11)):
            self.novedad(self.emp, date(2026, 10, dia), "ATRASO", minutos=minutos)
        self.novedad(self.emp, date(2026, 10, 7), "AUSENCIA")
        self.novedad(self.emp, date(2026, 10, 8), "AUSENCIA")
        e = calculo.estimar_mes(self.emp, *OCT)
        self.assertEqual([d.valor for d in e.atrasos], [D("1.00"), D("5.00"), D("5.00")])
        self.assertEqual(len(e.llamados_mes), 1)
        self.assertIsNone(e.tope_penalizaciones)  # v1 sin tope: 11 + 80 = 91
        self.assertEqual((e.total_descuentos, e.neto), (D("91.00"), D("309.00")))
        self.assertTrue(e.multas_superan_aviso)  # 91 > 10 % de 400

    def test_llamado_de_octubre_en_la_cuenta_anual_de_noviembre(self):
        """
        HALLAZGO: un atraso de 15 min en octubre fue llamado de atención con la v1 (más de 10 min),
        pero en noviembre la cuenta anual usa el tramo de la v2 (más de 29 min) y ya no lo cuenta.
        """
        self.novedad(self.emp, date(2026, 10, 6), "ATRASO", minutos=15)
        self.assertEqual(calculo.estimar_mes(self.emp, *OCT).llamados_ventana, 1)
        self.assertEqual(calculo.estimar_mes(self.emp, *NOV).llamados_ventana, 0)


class CajaChicaCuadreTests(TestCase):
    """Saldo actual = fondo inicial + ingresos − egresos, mes a mes, y el arqueo contra el registro."""

    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin.caja", None, "clave-segura-123", rol=Rol.ADMINISTRADOR)
        cls.hoy = timezone.localdate()

    def mov(self, tipo, valor, fecha):
        return caja.registrar(self.admin, fecha=fecha, tipo=tipo, concepto="Mov", valor=D(valor))

    def test_arrastre_entre_meses(self):
        primero = self.hoy.replace(day=1)
        anterior = (primero - timedelta(days=1)).replace(day=1)
        caja.fijar_fondo_inicial(self.admin, anterior.year, anterior.month, D("200"))
        self.mov(INGRESO, "25.50", anterior)
        self.mov(EGRESO, "80.25", anterior)
        self.mov(EGRESO, "19.99", anterior)
        rep_ant = caja.del_mes(anterior.year, anterior.month)
        # 200 + 25,50 − 80,25 − 19,99 = 125,26
        self.assertEqual((rep_ant.fondo_inicial, rep_ant.total_ingresos, rep_ant.total_egresos, rep_ant.saldo_final),
                         (D("200.00"), D("25.50"), D("100.24"), D("125.26")))
        # Mes actual: se repone hasta 200 → se asignan 74,74 nuevos
        rep = caja.fijar_fondo_inicial(self.admin, self.hoy.year, self.hoy.month, D("200"))
        self.assertEqual((rep.saldo_inicial, rep.total_fondos, rep.fondo_inicial), (D("125.26"), D("74.74"), D("200.00")))
        self.mov(EGRESO, "0.01", self.hoy)
        rep = caja.del_mes(self.hoy.year, self.hoy.month)
        self.assertEqual(rep.saldo_final, D("199.99"))
        self.assertEqual(caja.saldo(), rep.saldo_final)
        self.assertEqual(rep.filas[-1].saldo, D("199.99"))  # saldo de la fila = saldo de la hoja

    def test_anulado_no_mueve_el_saldo(self):
        self.mov(INGRESO, "100", self.hoy)
        gasto = self.mov(EGRESO, "33.33", self.hoy)
        caja.anular(self.admin, gasto, "Error")
        rep = caja.del_mes(self.hoy.year, self.hoy.month)
        self.assertEqual((rep.total_egresos, rep.saldo_final, rep.anulados, caja.saldo()), (D("0.00"), D("100.00"), 1, D("100.00")))

    def test_arqueo_con_sobrante_y_faltante(self):
        self.mov(INGRESO, "50", self.hoy)
        self.mov(EGRESO, "12.35", self.hoy)  # registro: 37,65
        sobra = caja.registrar_arqueo(self.admin, fecha=self.hoy, cantidades={"20": "1", "10": "1", "5": "1", "1": "3"})
        self.assertEqual((sobra.saldo_registro, sobra.total_contado, sobra.diferencia), (D("37.65"), D("38.00"), D("0.35")))
        falta = caja.registrar_arqueo(self.admin, fecha=self.hoy, cantidades={"20": "1", "10": "1", "5": "1", "1": "2",
                                                                               "0.25": "2", "0.10": "1", "0.05": "1"})
        # 20 + 10 + 5 + 2 + 0,50 + 0,10 + 0,05 = 37,65
        self.assertEqual((falta.total_contado, falta.diferencia), (D("37.65"), D("0.00")))

    def test_recibo_le_sobra_y_devuelve_todo(self):
        self.mov(INGRESO, "150", self.hoy)
        r = caja.crear_recibo(self.admin, fecha=self.hoy, entregado_a="Ashley Pérez")
        caja.agregar_linea(self.admin, r, fecha=self.hoy, concepto="Entrega", valor_entregado=D("100"))
        caja.agregar_linea(self.admin, r, fecha=self.hoy, concepto="Taxi", valor_gastado=D("12.50"))
        caja.agregar_linea(self.admin, r, fecha=self.hoy, concepto="Papelería", valor_gastado=D("57.25"))
        self.assertEqual(caja.saldo(), D("50.00"))
        self.assertEqual((r.total_entregado, r.total_gastado, r.valor_a_devolver, r.valor_a_reembolsar),
                         (D("100.00"), D("69.75"), D("30.25"), D("0")))
        caja.liquidar_recibo(self.admin, r, D("30.25"))
        self.assertEqual((r.saldo_pendiente, caja.saldo()), (D("0"), D("80.25")))  # 150 − 69,75
