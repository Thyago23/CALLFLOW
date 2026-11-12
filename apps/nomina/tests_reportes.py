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
from .models import ArqueoCaja, LineaRecibo, MovimientoCajaChica, NovedadNomina, ReciboCaja, PeriodoNomina, TipoNovedadNomina

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
    def post_mov(self, tipo, concepto, valor, **extra):
        datos = {"fecha": f"{self.hoy:%Y-%m-%d}", "tipo": tipo, "categoria": "OTROS", "concepto": concepto, "valor": valor,
                 "forma_pago": "EFECTIVO"} | extra
        return self.client.post(reverse("nomina:caja_chica"), datos, follow=True)

    def test_solo_el_administrador(self):
        m = self.mov(INGRESO, "10")
        for usuario in (self.vendedor, self.encargado):
            self.client.force_login(usuario)
            for url in (reverse("nomina:caja_chica"), reverse("nomina:caja_reporte"), reverse("nomina:caja_arqueo"),
                        reverse("nomina:caja_recibo", args=[m.pk])):
                self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_registrar_anular_e_imprimir_la_hoja_del_mes(self):
        self.client.force_login(self.admin)
        self.post_mov(INGRESO, "Fondo de caja chica", "150")
        respuesta = self.post_mov(EGRESO, "Cafetería", "12.40", categoria="ALIMENTACION", beneficiario="Supermaxi",
                                  comprobante="001-002-123")
        self.assertContains(respuesta, "Saldo actual: $137,60")
        self.assertContains(respuesta, "Alimentación")
        sin_saldo = self.post_mov(EGRESO, "Monitor", "500")
        self.assertContains(sin_saldo, "Saldo insuficiente")

        gasto = MovimientoCajaChica.objects.get(concepto="Cafetería")
        self.assertEqual((gasto.categoria, gasto.forma_pago), ("ALIMENTACION", "EFECTIVO"))
        self.client.post(reverse("nomina:caja_anular", args=[gasto.pk]), {"motivo": "Lo pagó la empresa", "volver": "https://otro.sitio/"})
        gasto.refresh_from_db()
        self.assertTrue(gasto.anulado)

        mes = f"{self.hoy:%Y-%m}"
        hoja = self.client.get(reverse("nomina:caja_reporte"), {"mes": mes})
        for texto in ("Registro y control de caja chica", "MULTIVISAS", "Fondo de caja chica", "Firma / V.º B.º", "Elaborado por",
                      "Revisado por", "Aprobado por"):
            self.assertContains(hoja, texto)
        self.assertNotContains(hoja, "Cafetería")  # anulado: oculto salvo que se pida
        con_anulados = self.client.get(reverse("nomina:caja_reporte"), {"mes": mes, "anulados": "1"})
        self.assertContains(con_anulados, "ANULADO: Lo pagó la empresa")

    def test_fondo_inicial_es_el_saldo_del_mes_anterior(self):
        primero = self.hoy.replace(day=1)
        anterior = primero - timedelta(days=1)
        self.mov(INGRESO, "170", anterior, "Fondo")
        self.mov(EGRESO, "20", anterior, "Gasto del mes pasado")
        self.mov(INGRESO, "30", self.hoy, "Reposición")
        self.mov(EGRESO, "22.04", self.hoy, "Pago al Municipio")
        rep = caja.del_mes(self.hoy.year, self.hoy.month)
        self.assertEqual((rep.saldo_inicial, rep.total_ingresos, rep.total_egresos, rep.saldo_final),
                         (D("150.00"), D("30.00"), D("22.04"), D("157.96")))

    def test_el_fondo_va_en_fondo_inicial_como_en_el_excel(self):
        """Octubre del Excel: fondo $170, ingresos $0, egresos $157,15, saldo actual $12,85."""
        primero = self.hoy.replace(day=1)
        caja.fijar_fondo_inicial(self.admin, self.hoy.year, self.hoy.month, D("170"))
        for valor in ("22.04", "16.18", "1.94", "11.79", "1.41", "28", "2.95", "58.80", "10.25", "3.79"):
            self.mov(EGRESO, valor, primero)
        rep = caja.del_mes(self.hoy.year, self.hoy.month)
        self.assertEqual((rep.fondo_inicial, rep.total_ingresos, rep.total_egresos, rep.saldo_final),
                         (D("170.00"), D("0.00"), D("157.15"), D("12.85")))
        self.assertEqual(caja.saldo(), D("12.85"))  # la base suma el fondo y resta los egresos
        with self.assertRaises(ValidationError):  # no se gasta más de lo que hay
            self.mov(EGRESO, "12.86", primero)
        self.client.force_login(self.admin)
        hoja = self.client.get(reverse("nomina:caja_chica"), {"mes": f"{self.hoy:%Y-%m}"})
        for texto in ("Registro y control de caja chica", "$170,00", "$157,15", "$12,85", "Arqueo de caja"):
            self.assertContains(hoja, texto)

    def test_fondo_inicial_editable(self):
        primero = self.hoy.replace(day=1)
        self.client.force_login(self.admin)
        url, mes = reverse("nomina:caja_fondo"), f"{self.hoy:%Y-%m}"
        self.assertContains(self.client.post(url, {"mes": mes, "valor": "170"}, follow=True), "Fondo inicial de")
        self.mov(EGRESO, "157.15", primero)
        # Se corrige: la asignación anterior queda anulada con motivo y el saldo sigue la cuenta del Excel.
        self.client.post(url, {"mes": mes, "valor": "200,50"})
        rep = caja.del_mes(self.hoy.year, self.hoy.month)
        self.assertEqual((rep.fondo_inicial, rep.saldo_final), (D("200.50"), D("43.35")))
        fondos = MovimientoCajaChica.objects.filter(tipo=MovimientoCajaChica.Tipo.FONDO).order_by("pk")
        self.assertEqual([(f.valor, f.anulado) for f in fondos], [(D("170.00"), True), (D("200.50"), False)])
        self.assertEqual(fondos[0].motivo_anulacion, "Fondo inicial cambiado a $200,50")
        self.assertEqual(len(rep.filas), 1)  # el fondo no es una fila de la hoja
        # No alcanza para lo ya gastado: no cambia nada.
        self.assertContains(self.client.post(url, {"mes": mes, "valor": "150"}, follow=True), "quedaría negativo")
        self.assertEqual(caja.del_mes(self.hoy.year, self.hoy.month).fondo_inicial, D("200.50"))
        self.assertContains(self.client.post(url, {"mes": mes, "valor": "abc"}, follow=True), "como un número")
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.post(url, {"mes": mes, "valor": "1"}).status_code, 403)

    def test_el_fondo_no_puede_ser_menor_que_lo_que_quedo(self):
        primero = self.hoy.replace(day=1)
        self.mov(INGRESO, "50", primero - timedelta(days=1))
        with self.assertRaises(ValidationError):
            caja.fijar_fondo_inicial(self.admin, self.hoy.year, self.hoy.month, D("20"))
        rep = caja.fijar_fondo_inicial(self.admin, self.hoy.year, self.hoy.month, D("170"))
        self.assertEqual((rep.saldo_inicial, rep.total_fondos, rep.fondo_inicial), (D("50.00"), D("120.00"), D("170.00")))
        with self.assertRaises(ValidationError):  # un mes que no empezó
            siguiente = (primero + timedelta(days=32)).replace(day=1)
            caja.fijar_fondo_inicial(self.admin, siguiente.year, siguiente.month, D("170"))

    def test_recibo_con_valor_en_letras_y_dos_copias(self):
        self.client.force_login(self.admin)
        self.mov(INGRESO, "100")
        gasto = caja.registrar(self.admin, fecha=self.hoy, tipo=EGRESO, concepto="Compra de colada", valor=D("58.80"),
                               beneficiario="Panadería el Éxito", categoria="ALIMENTACION")
        recibo = self.client.get(reverse("nomina:caja_recibo", args=[gasto.pk]))
        self.assertContains(recibo, f"N.º {gasto.numero_recibo}")
        self.assertContains(recibo, "CINCUENTA Y OCHO DÓLARES CON 80/100", count=2)
        self.assertContains(recibo, "Panadería el Éxito", count=2)
        self.assertContains(recibo, "$41,20")  # saldo de caja después del gasto
        caja.anular(self.admin, gasto, "Duplicado")
        self.assertContains(self.client.get(reverse("nomina:caja_recibo", args=[gasto.pk])), "sello-anulado")


class ArqueoCajaTests(BaseReportes):
    def test_la_base_calcula_saldo_total_y_diferencia(self):
        self.mov(INGRESO, "170")
        self.mov(EGRESO, "157.11")
        arqueo = caja.registrar_arqueo(self.admin, fecha=self.hoy, cantidades={
            "5": "1", "1": "4", "0.50": "4", "0.25": "5", "0.10": "2", "0.05": "8", "0.01": "4", "100": ""})
        self.assertEqual((arqueo.saldo_registro, arqueo.total_contado, arqueo.diferencia), (D("12.89"), D("12.89"), D("0.00")))
        self.assertTrue(arqueo.cuadra)
        # Aunque se manden otros totales, la base los recalcula.
        falso = ArqueoCaja.objects.create(fecha=self.hoy, responsable=self.admin, conteo={"20": 1}, total_contado=D("999"),
                                          saldo_registro=D("1"), diferencia=D("998"))
        falso.refresh_from_db()
        self.assertEqual((falso.saldo_registro, falso.total_contado, falso.diferencia), (D("12.89"), D("20.00"), D("7.11")))

    def test_inmutable_y_conteo_valido(self):
        arqueo = caja.registrar_arqueo(self.admin, fecha=self.hoy, cantidades={"20": "1"})
        with self.assertRaises(DatabaseError), transaction.atomic():
            ArqueoCaja.objects.filter(pk=arqueo.pk).update(observaciones="cambio")
        with self.assertRaises(DatabaseError), transaction.atomic():
            ArqueoCaja.objects.filter(pk=arqueo.pk).delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            ArqueoCaja.objects.create(fecha=self.hoy, responsable=self.admin, conteo={"3": 1})
        for malo in ({"20": "-1"}, {"20": "1.5"}, {"20": "x"}):
            with self.assertRaises(ValidationError):
                caja.registrar_arqueo(self.admin, fecha=self.hoy, cantidades=malo)

    def test_pantalla_e_impresion(self):
        self.client.force_login(self.admin)
        self.mov(INGRESO, "50")
        respuesta = self.client.post(reverse("nomina:caja_arqueo"), {"fecha": f"{self.hoy:%Y-%m-%d}", "cantidad_20": "2",
                                                                     "cantidad_5": "1", "observaciones": "Faltan $5"}, follow=True)
        self.assertContains(respuesta, "diferencia de -$5,00")
        self.assertContains(respuesta, "Arqueo y cierre de caja chica")
        self.assertContains(respuesta, "Responsable de caja")
        self.assertContains(self.client.get(reverse("nomina:caja_arqueo")), "Falta $5,00")


    def test_enlaces_en_el_menu_de_nomina(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("nomina:resumen"))
        self.assertContains(respuesta, reverse("nomina:caja_chica"))
        self.assertContains(respuesta, reverse("nomina:reporte_roles"))


class ReciboCajaTests(BaseReportes):
    """Excel «Recibo Caja Chica»: entrega a rendir que mueve la caja."""

    def setUp(self):
        self.mov(INGRESO, "170", self.hoy, "Fondo")
        self.recibo = caja.crear_recibo(self.admin, fecha=self.hoy, entregado_a="Ashley Pérez", area="Logística",
                                        autorizado_por="Diego Mendoza")

    def linea(self, entregado=None, gastado=None, concepto="Fila"):
        return caja.agregar_linea(self.admin, self.recibo, fecha=self.hoy, concepto=concepto,
                                  valor_entregado=D(entregado) if entregado else None, valor_gastado=D(gastado) if gastado else None)

    def test_gasta_mas_de_lo_entregado_se_reembolsa(self):
        self.linea(entregado="50")
        self.assertEqual(caja.saldo(), D("120.00"))  # lo entregado sale de la caja al momento
        self.linea(gastado="3.79")
        self.linea(gastado="58.80")
        r = self.recibo
        self.assertEqual((r.total_entregado, r.total_gastado, r.valor_a_devolver, r.valor_a_reembolsar), (D("50.00"), D("62.59"), D("0"), D("12.59")))
        caja.liquidar_recibo(self.admin, r, D("0"))
        self.assertEqual((r.estado, r.valor_reembolsado, r.saldo_pendiente), (ReciboCaja.Estado.LIQUIDADO, D("12.59"), D("0")))
        self.assertEqual(caja.saldo(), D("107.41"))  # 170 − 50 − 12,59
        with self.assertRaises(ValidationError):
            self.linea(gastado="1")

    def test_le_sobra_y_devuelve_en_partes(self):
        self.linea(entregado="40")
        self.linea(gastado="25.50")
        with self.assertRaises(ValidationError):  # no devuelve más de lo que corresponde
            caja.liquidar_recibo(self.admin, self.recibo, D("20"))
        caja.liquidar_recibo(self.admin, self.recibo, D("10"))
        self.assertEqual((self.recibo.saldo_pendiente, caja.saldo()), (D("4.50"), D("140.00")))
        with self.assertRaises(ValidationError):
            caja.registrar_devolucion(self.admin, self.recibo, D("5"))
        caja.registrar_devolucion(self.admin, self.recibo, D("4.50"))
        self.assertEqual((self.recibo.valor_devuelto, self.recibo.saldo_pendiente, caja.saldo()), (D("14.50"), D("0"), D("144.50")))
        self.assertEqual(MovimientoCajaChica.objects.filter(recibo=self.recibo, anulado=False).count(), 3)

    def test_quitar_fila_devuelve_lo_entregado_a_la_caja(self):
        fila = self.linea(entregado="5", concepto="Errada")
        caja.anular_linea(self.admin, fila)
        fila.movimiento.refresh_from_db()
        self.assertTrue(fila.movimiento.anulado)
        self.assertEqual((caja.saldo(), self.recibo.total_entregado), (D("170.00"), D("0")))
        with self.assertRaises(ValidationError):
            self.linea(entregado="500")  # no alcanza la caja
        with self.assertRaises(ValidationError):
            self.linea()  # sin valor

    def test_la_base_protege_el_recibo(self):
        fila = self.linea(entregado="40")
        self.linea(gastado="25.50")
        with self.assertRaises(DatabaseError), transaction.atomic():
            LineaRecibo.objects.filter(pk=fila.pk).delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            LineaRecibo.objects.filter(pk=fila.pk).update(valor_entregado=D("1"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            ReciboCaja.objects.filter(pk=self.recibo.pk).update(valor_devuelto=D("1"))  # abierto: sin devoluciones
        caja.liquidar_recibo(self.admin, self.recibo, D("14.50"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            ReciboCaja.objects.filter(pk=self.recibo.pk).update(estado="ABIERTO", valor_devuelto=0, valor_reembolsado=0, liquidado_en=None)
        with self.assertRaises(DatabaseError), transaction.atomic():
            ReciboCaja.objects.filter(pk=self.recibo.pk).update(valor_devuelto=D("99"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            LineaRecibo.objects.create(recibo=self.recibo, fecha=self.hoy, concepto="tarde", valor_gastado=D("1"))

    def test_pantallas_e_impresion(self):
        self.client.force_login(self.admin)
        url = reverse("nomina:caja_recibo_entrega", args=[self.recibo.pk])
        self.client.post(url, {"accion": "linea", "fecha": f"{self.hoy:%Y-%m-%d}", "concepto": "Entrega para compras", "valor_entregado": "50"})
        self.client.post(url, {"accion": "linea", "fecha": f"{self.hoy:%Y-%m-%d}", "concepto": "Taxi", "valor_gastado": "62.59"})
        pagina = self.client.get(url)
        for texto in ("MULTIVISA", "Recibo y control de caja chica", "N.º de recibo:", "Área / Departamento:", "Concepto / Detalle del gasto",
                      "Resumen y liquidación", "Valor adicional a reembolsar", "Saldo pendiente de devolución", "Revisado / Autorizado por",
                      "Nombre y firma", "$12,59"):
            self.assertContains(pagina, texto)
        respuesta = self.client.post(url, {"accion": "liquidar", "valor_devuelto": "0"}, follow=True)
        self.assertContains(respuesta, "liquidado")
        self.assertContains(self.client.get(reverse("nomina:caja_recibos")), "Reembolsar $12,59")
        entrega = MovimientoCajaChica.objects.filter(recibo=self.recibo).first()
        self.assertRedirects(self.client.get(reverse("nomina:caja_recibo", args=[entrega.pk])), url)
        gasto = self.mov(EGRESO, "22.04", self.hoy, "Pago al Municipio")
        self.assertContains(self.client.get(reverse("nomina:caja_recibo", args=[gasto.pk])), "Pago al Municipio")
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(reverse("nomina:caja_recibos")).status_code, 403)
        self.assertEqual(self.client.post(url, {"accion": "devolucion", "valor_devuelto": "1"}).status_code, 403)

