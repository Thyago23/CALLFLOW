"""
Reportes para imprimir de Nómina (solo administrador): rol de pagos y caja chica.

    python manage.py test apps.nomina
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import RegistroCambio, Rol, Usuario
from apps.personal.models import Cargo, Empleado, HistorialSalario

from . import caja_chica as caja
from . import calculo
from .models import MovimientoCajaChica, NovedadNomina, PeriodoNomina, TipoNovedadNomina

INGRESO, EGRESO = MovimientoCajaChica.Tipo.INGRESO, MovimientoCajaChica.Tipo.EGRESO


class BaseReportes(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("diego.mendoza", None, "x", first_name="Diego", last_name="Mendoza",
                                                rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("lucia.mora", None, "x", first_name="Lucía", last_name="Mora", rol=Rol.VENDEDOR)
        cls.encargado = Usuario.objects.create_user("ana.torres", None, "x", rol=Rol.ENCARGADO_CIERRE)
        cls.hoy = timezone.localdate()
        cargo = Cargo.objects.get(nombre="Asesor de ventas")
        for cedula, nombres, apellidos, sueldo in [("1712345675", "Lucía", "Mora", "460"), ("0912345675", "Pedro", "Paz", "400")]:
            e = Empleado.objects.create(identificacion=cedula, nombres=nombres, apellidos=apellidos, cargo=cargo,
                                        fecha_ingreso=date(2026, 1, 5))
            HistorialSalario.objects.create(empleado=e, salario_base=D(sueldo), vigente_desde=date(2026, 1, 5))

    def mov(self, tipo, valor, fecha=None, concepto="Movimiento"):
        return caja.registrar(self.admin, fecha=fecha or self.hoy, tipo=tipo, concepto=concepto, valor=D(valor))


class ReporteRolesTests(BaseReportes):
    def test_solo_el_administrador(self):
        for usuario in (self.vendedor, self.encargado):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("nomina:reporte_roles")).status_code, 403)

    def test_cuadro_general_con_totales_y_firmas(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("nomina:reporte_roles"), {"mes": f"{self.hoy:%Y-%m}"})
        self.assertContains(respuesta, "Rol de pagos general")
        self.assertContains(respuesta, "Mora Lucía")
        self.assertContains(respuesta, "Paz Pedro")
        self.assertContains(respuesta, "$860,00", count=2)  # sueldos y neto (sin descuentos)
        self.assertContains(respuesta, "Firma")
        self.assertContains(respuesta, "window.print()")

    def test_rol_por_empleado_con_toda_la_informacion_y_sin_comision(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("nomina:reporte_roles"), {"mes": f"{self.hoy:%Y-%m}", "formato": "individual"})
        self.assertContains(respuesta, 'class="rol-pago rol-individual pagina-vertical"', count=2)
        for texto in ("Rol de pagos", "Empleado", "Cédula", "Cargo", "Equipo", "Sueldo base", "Total ingresos", "Total descuentos",
                      "Neto a recibir", "Faltas justificadas", "Faltas injustificadas", "Atrasos", "Días trabajados",
                      "Firma Empleado", "Firma Contabilidad"):
            self.assertContains(respuesta, texto)
        self.assertContains(respuesta, calculo.nombre_mes(self.hoy.year, self.hoy.month))
        self.assertContains(respuesta, "$460,00")
        self.assertNotContains(respuesta, "Comprobante de comisión")

    def test_imprimir_un_solo_empleado(self):
        lucia = Empleado.objects.get(identificacion="1712345675")
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("nomina:reporte_roles"), {"mes": f"{self.hoy:%Y-%m}", "empleado": lucia.pk})
        self.assertContains(respuesta, 'class="rol-pago rol-individual pagina-vertical"', count=1)
        self.assertContains(respuesta, "Lucía Mora")
        self.assertNotContains(respuesta, "Pedro Paz")

    def test_anticipo_aprobado_se_descuenta(self):
        lucia = Empleado.objects.get(identificacion="1712345675")
        inicio = self.hoy.replace(day=1)
        periodo = PeriodoNomina.objects.create(nombre="Mes", fecha_inicio=inicio, fecha_fin=calculo.ultimo_dia(inicio.year, inicio.month))
        NovedadNomina.objects.create(empleado=lucia, periodo=periodo, tipo=TipoNovedadNomina.objects.get(codigo="ANTICIPO"),
                                     valor=D("60"), estado="APROBADA", aprobada_en=timezone.now())
        NovedadNomina.objects.create(empleado=lucia, periodo=periodo, tipo=TipoNovedadNomina.objects.get(codigo="ANTICIPO"),
                                     valor=D("999"))  # pendiente: no se descuenta
        e = calculo.estimar_mes(lucia, self.hoy.year, self.hoy.month)
        self.assertEqual((e.total_anticipos, e.neto), (D("60.00"), D("400.00")))
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("nomina:reporte_roles"), {"mes": f"{self.hoy:%Y-%m}", "empleado": lucia.pk})
        self.assertContains(respuesta, "$60,00")

    def test_pdf_del_rol(self):
        lucia = Empleado.objects.get(identificacion="1712345675")
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("nomina:rol_pdf", args=[lucia.pk]), {"mes": f"{self.hoy:%Y-%m}"})
        self.assertEqual(respuesta["Content-Type"], "application/pdf")
        self.assertTrue(respuesta.content.startswith(b"%PDF"))


class DiasTrabajadosTests(BaseReportes):
    def test_mes_comercial_de_30_dias(self):
        e = Empleado.objects.get(identificacion="1712345675")
        self.assertEqual(calculo.dias_trabajados(e, 2026, 2, 0), 30)  # febrero también cuenta 30
        self.assertEqual(calculo.dias_trabajados(e, 2026, 3, 2), 28)  # menos las faltas no justificadas
        e.fecha_ingreso = date(2026, 3, 16)
        self.assertEqual(calculo.dias_trabajados(e, 2026, 3, 0), 15)  # ingresó a mitad de mes
        e.fecha_ingreso, e.fecha_salida = date(2026, 1, 5), date(2026, 3, 10)
        self.assertEqual(calculo.dias_trabajados(e, 2026, 3, 0), 10)  # salió el 10
        self.assertEqual(calculo.dias_trabajados(e, 2026, 4, 0), 0)


class CajaChicaReglasTests(BaseReportes):
    def test_saldo_y_reporte_con_saldo_acumulado(self):
        ayer = self.hoy - timedelta(days=1)
        self.mov(INGRESO, "200", ayer, "Fondo inicial")
        self.mov(EGRESO, "15.50", self.hoy, "Agua")
        self.mov(EGRESO, "4.50", self.hoy, "Esferos")
        self.assertEqual(caja.saldo(), D("180.00"))
        rep = caja.reporte(self.hoy, self.hoy)
        self.assertEqual(rep.saldo_inicial, D("200.00"))
        self.assertEqual([f.saldo for f in rep.filas], [D("184.50"), D("180.00")])
        self.assertEqual((rep.total_ingresos, rep.total_egresos, rep.saldo_final), (D("0"), D("20.00"), D("180.00")))

    def test_no_se_gasta_mas_de_lo_que_hay(self):
        self.mov(INGRESO, "10")
        with self.assertRaises(ValidationError):
            self.mov(EGRESO, "10.01")
        # La base de datos también lo impide, aunque se salte la aplicación.
        with self.assertRaises(DatabaseError), transaction.atomic():
            MovimientoCajaChica.objects.create(fecha=self.hoy, tipo=EGRESO, concepto="x", valor=D("50"))

    def test_no_se_modifica_ni_se_borra(self):
        m = self.mov(INGRESO, "100")
        m.valor = D("1000")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.save()
        with self.assertRaises(DatabaseError), transaction.atomic():
            MovimientoCajaChica.objects.filter(pk=m.pk).delete()

    def test_anular_con_motivo_y_queda_auditado(self):
        self.mov(INGRESO, "100")
        gasto = self.mov(EGRESO, "30")
        with self.assertRaises(ValidationError):
            caja.anular(self.admin, gasto, "")
        caja.anular(self.admin, gasto, "Registrado dos veces")
        self.assertEqual(caja.saldo(), D("100.00"))
        self.assertTrue(RegistroCambio.objects.filter(tabla="nomina_movimientocajachica", accion="UPDATE").exists())
        with self.assertRaises(ValidationError):
            caja.anular(self.admin, gasto, "otra vez")

    def test_no_se_anula_un_ingreso_ya_gastado(self):
        fondo = self.mov(INGRESO, "50")
        self.mov(EGRESO, "40")
        with self.assertRaises(ValidationError):
            caja.anular(self.admin, fondo, "error")
        fondo.refresh_from_db()
        self.assertFalse(fondo.anulado)

    def test_fecha_futura(self):
        with self.assertRaises(ValidationError):
            self.mov(INGRESO, "10", self.hoy + timedelta(days=1))


class CajaChicaVistasTests(BaseReportes):
    def test_solo_el_administrador(self):
        for usuario in (self.vendedor, self.encargado):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("nomina:caja_chica")).status_code, 403)
            self.assertEqual(self.client.get(reverse("nomina:caja_reporte")).status_code, 403)

    def test_registrar_anular_e_imprimir(self):
        self.client.force_login(self.admin)
        url = reverse("nomina:caja_chica")
        self.client.post(url, {"fecha": f"{self.hoy:%Y-%m-%d}", "tipo": INGRESO, "concepto": "Fondo de caja chica", "valor": "150"})
        respuesta = self.client.post(url, {"fecha": f"{self.hoy:%Y-%m-%d}", "tipo": EGRESO, "concepto": "Cafetería",
                                           "beneficiario": "Supermaxi", "comprobante": "001-002-123", "valor": "12.40"}, follow=True)
        self.assertContains(respuesta, "Saldo actual: $137,60")
        sin_saldo = self.client.post(url, {"fecha": f"{self.hoy:%Y-%m-%d}", "tipo": EGRESO, "concepto": "Monitor", "valor": "500"})
        self.assertContains(sin_saldo, "Saldo insuficiente")

        gasto = MovimientoCajaChica.objects.get(concepto="Cafetería")
        self.client.post(reverse("nomina:caja_anular", args=[gasto.pk]), {"motivo": "Lo pagó la empresa", "volver": "https://otro.sitio/"})
        gasto.refresh_from_db()
        self.assertTrue(gasto.anulado)

        reporte = self.client.get(reverse("nomina:caja_reporte"), {"desde": f"{self.hoy:%Y-%m-%d}", "hasta": f"{self.hoy:%Y-%m-%d}"})
        self.assertContains(reporte, "Reporte de caja chica")
        self.assertContains(reporte, "Fondo de caja chica")
        self.assertNotContains(reporte, "Cafetería")  # anulado: oculto salvo que se pida
        self.assertContains(reporte, "Elaborado por")
        con_anulados = self.client.get(reverse("nomina:caja_reporte"), {"desde": f"{self.hoy:%Y-%m-%d}", "anulados": "1"})
        self.assertContains(con_anulados, "ANULADO: Lo pagó la empresa")

    def test_enlaces_en_el_menu_de_nomina(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("nomina:resumen"))
        self.assertContains(respuesta, reverse("nomina:caja_chica"))
        self.assertContains(respuesta, reverse("nomina:reporte_roles"))
