"""Contabilidad de Multivisas y T&T: rol Contador, caja y bancos, libro diario, balance, leads y clientes."""
import tempfile
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.cuentas import rol_para_cargo
from apps.core.models import Rol, Usuario
from apps.core.modulos import modulos_de

from . import servicios
from .empresa import CLAVE_SESION
from .forms import ruc_valido
from .models import (ActivoFijo, Asiento, Cliente, CuentaContable, CuentaFinanciera, Empresa, Lead, LineaAsiento,
                     MovimientoFinanciero, Proveedor)

Tipo = MovimientoFinanciero.Tipo
HOY = date.today()


class BaseContabilidad(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.contadora = Usuario.objects.create_user("ana.contadora", None, "x", rol=Rol.CONTADOR)
        cls.admin = Usuario.objects.create_user("gabo.admin", None, "x", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vera.ventas", None, "x", rol=Rol.VENDEDOR)
        cls.multivisas = Empresa.objects.get(codigo="MULTIVISAS")
        cls.tyt = Empresa.objects.get(codigo="TYT")
        cls.c = {c.codigo: c for c in CuentaContable.objects.all()}

    def caja(self, empresa=None, saldo=D("100"), nombre="Caja general", tipo=CuentaFinanciera.Tipo.CAJA, fecha=None):
        cuenta = "1.1.01.01" if tipo == CuentaFinanciera.Tipo.CAJA else "1.1.02.01"
        return CuentaFinanciera.objects.create(
            empresa=empresa or self.multivisas, nombre=nombre, tipo=tipo, banco="" if tipo == "CAJA" else "Pichincha",
            cuenta_contable=self.c[cuenta], saldo_inicial=saldo, fecha_saldo_inicial=fecha or HOY - timedelta(days=30))

    def mov(self, cuenta, tipo, valor, **extra):
        return servicios.registrar_movimiento(self.contadora, cuenta=cuenta, fecha=extra.pop("fecha", HOY), tipo=tipo,
                                              concepto=extra.pop("concepto", "Prueba"), valor=D(valor), **extra)

    def asiento(self, empresa=None, lineas=(("1.1.01.01", "50", "0"), ("4.1.01.02", "0", "50")), **extra):
        a = Asiento(empresa=empresa or self.multivisas, fecha=extra.pop("fecha", HOY), concepto="Cobro de trámite", **extra)
        return servicios.guardar_borrador(a, [{"cuenta": self.c[cod], "debe": D(d), "haber": D(h)} for cod, d, h in lineas],
                                          self.contadora)

    def cliente(self, empresa=None, identificacion="1710034065", **extra):
        return servicios.guardar_con_codigo(Cliente(empresa=empresa or self.multivisas, nombres="María Pérez",
                                                    identificacion=identificacion, **extra), "CLI")


# --------------------------------------------------------------------------- rol
class RolContadorTests(BaseContabilidad):
    def test_cargo_contador_recibe_el_rol(self):
        self.assertEqual(rol_para_cargo("Contador"), Rol.CONTADOR)
        self.assertEqual(rol_para_cargo("Contadora administrativa"), Rol.CONTADOR)
        self.assertEqual(rol_para_cargo("Asistente contable"), Rol.CONTADOR)
        self.assertEqual(rol_para_cargo("Administrador"), Rol.ADMINISTRADOR)

    def test_modulos_del_contador(self):
        claves = {m.clave for m in modulos_de(self.contadora)}
        self.assertTrue({"contabilidad", "crm", "nomina", "personal", "agenda", "reportes", "soporte"} <= claves)
        self.assertFalse(claves & {"ventas", "cierre", "tablero", "chips", "breaks", "ti", "mensajes", "inventario"})

    def test_permisos_del_contador(self):
        self.assertTrue(self.contadora.has_perm("nomina.add_movimientocajachica"))
        self.assertTrue(self.contadora.has_perm("personal.view_empleado"))
        self.assertFalse(self.contadora.has_perm("personal.change_empleado"))  # salarios: solo consulta
        self.assertFalse(self.contadora.has_perm("nomina.delete_periodonomina"))
        self.assertFalse(self.contadora.has_perm("ventas.view_formulariodami"))
        # El administrador consulta la contabilidad; no la registra.
        self.assertTrue(self.admin.has_perm("contabilidad.view_asiento"))
        self.assertFalse(self.admin.has_perm("contabilidad.add_asiento"))

    def test_reportes_de_nomina_y_no_los_de_ventas(self):
        from apps.reportes.models import TipoReporte

        del_contador = set(TipoReporte.objects.filter(roles_permitidos__contains=[Rol.CONTADOR]).values_list("codigo", flat=True))
        self.assertEqual(del_contador, {"NOMINA_RESUMEN", "NOMINA_COMISIONES", "NOMINA_ROL_PAGO"})

    def test_vendedor_no_entra(self):
        self.client.force_login(self.vendedor)
        for nombre in ("contabilidad:inicio", "contabilidad:diario", "crm:leads", "crm:clientes"):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)


# --------------------------------------------------------------------------- plan de cuentas
class PlanCuentasTests(BaseContabilidad):
    def test_plan_del_excel_cargado_y_carga_idempotente(self):
        self.assertEqual(CuentaContable.objects.count(), 117)
        self.assertEqual(servicios.cargar_plan_cuentas(), 0)
        caja = self.c["1.1.01.01"]
        self.assertEqual((caja.nombre, caja.tipo, caja.naturaleza), ("Caja general", "ACTIVO", "DEUDORA"))
        self.assertEqual(self.c["5.1.01"].tipo, "COSTO")

    def test_solo_las_cuentas_hoja_son_de_movimiento(self):
        de_movimiento = set(servicios.cuentas_de_movimiento().values_list("codigo", flat=True))
        self.assertIn("1.1.01.01", de_movimiento)
        self.assertIn("6.1.01", de_movimiento)
        self.assertNotIn("1.1.01", de_movimiento)
        self.assertNotIn("1", de_movimiento)
        self.assertFalse(self.c["1.1"].es_de_movimiento)

    def test_nueva_subcuenta_exige_grupo_existente(self):
        self.client.force_login(self.contadora)
        r = self.client.post(reverse("contabilidad:cuenta_crear"), {
            "codigo": "9.9.9", "nombre": "X", "tipo": "INGRESO", "naturaleza": "ACREEDORA"})
        self.assertIn("No existe la cuenta de grupo", r.context["form"].errors["codigo"][0])
        r = self.client.post(reverse("contabilidad:cuenta_crear"), {
            "codigo": "4.1.01.08", "nombre": "Ingresos por paquetes turísticos", "tipo": "INGRESO",
            "naturaleza": "ACREEDORA", "activa": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("4.1.01", set(servicios.cuentas_de_movimiento().values_list("codigo", flat=True)))


# --------------------------------------------------------------------------- caja y bancos
class CajaBancosTests(BaseContabilidad):
    def test_saldo_con_ingresos_y_egresos(self):
        caja = self.caja()
        self.mov(caja, Tipo.INGRESO, "50")
        self.mov(caja, Tipo.EGRESO, "30")
        self.assertEqual(servicios.saldo(caja), D("120"))
        anterior, filas, final = servicios.estado_de_cuenta(caja)
        self.assertEqual((anterior, [f.saldo for f in filas], final), (D("100"), [D("150"), D("120")], D("120")))

    def test_la_caja_no_queda_en_negativo(self):
        caja = self.caja(saldo=D("10"))
        with self.assertRaisesMessage(ValidationError, "Saldo insuficiente"):
            self.mov(caja, Tipo.EGRESO, "10.01")
        # Ni saltándose la aplicación: lo impide la base.
        with self.assertRaisesMessage(DatabaseError, "Saldo insuficiente"), transaction.atomic():
            MovimientoFinanciero.objects.create(cuenta=caja, fecha=HOY, tipo=Tipo.EGRESO, concepto="x", valor=D("11"))

    def test_el_banco_si_puede_quedar_en_sobregiro(self):
        banco = self.caja(saldo=D("0"), nombre="Pichincha", tipo=CuentaFinanciera.Tipo.CORRIENTE)
        self.mov(banco, Tipo.TRANSFERENCIA_ENVIADA, "20")
        self.assertEqual(servicios.saldo(banco), D("-20"))

    def test_movimiento_no_se_edita_ni_se_borra(self):
        m = self.mov(self.caja(), Tipo.INGRESO, "50")
        with self.assertRaisesMessage(DatabaseError, "no se modifican"), transaction.atomic():
            MovimientoFinanciero.objects.filter(pk=m.pk).update(valor=D("60"))
        with self.assertRaisesMessage(DatabaseError, "no se borran"), transaction.atomic():
            MovimientoFinanciero.objects.filter(pk=m.pk).delete()

    def test_fecha_anterior_al_saldo_inicial_y_fecha_futura(self):
        caja = self.caja(fecha=HOY - timedelta(days=5))
        with self.assertRaisesMessage(ValidationError, "anterior al saldo inicial"):
            self.mov(caja, Tipo.INGRESO, "5", fecha=HOY - timedelta(days=6))
        with self.assertRaises(ValidationError):
            self.mov(caja, Tipo.INGRESO, "5", fecha=HOY + timedelta(days=1))

    def test_anular_y_no_dejar_la_caja_en_negativo(self):
        caja = self.caja(saldo=D("0"))
        ingreso = self.mov(caja, Tipo.INGRESO, "50")
        self.mov(caja, Tipo.EGRESO, "40")
        with self.assertRaisesMessage(ValidationError, "quedaría en negativo"):
            servicios.anular_movimiento(self.contadora, ingreso, "Error de digitación")
        with self.assertRaisesMessage(ValidationError, "motivo"):
            servicios.anular_movimiento(self.contadora, ingreso, "  ")
        egreso = caja.movimientos.get(tipo=Tipo.EGRESO)
        servicios.anular_movimiento(self.contadora, egreso, "Duplicado")
        self.assertEqual(servicios.saldo(caja), D("50"))
        with self.assertRaisesMessage(DatabaseError, "anulado"), transaction.atomic():
            MovimientoFinanciero.objects.filter(pk=egreso.pk).update(anulado=False)

    def test_traspaso_de_caja_a_banco(self):
        caja = self.caja()
        banco = self.caja(saldo=D("0"), nombre="Pichincha", tipo=CuentaFinanciera.Tipo.CORRIENTE)
        salida = servicios.registrar_traspaso(self.contadora, origen=caja, destino=banco, fecha=HOY, valor=D("80"),
                                              concepto="Depósito de la caja", referencia="P-1")
        self.assertEqual((servicios.saldo(caja), servicios.saldo(banco)), (D("20"), D("80")))
        self.assertEqual(salida.contraparte.cuenta, banco)
        self.assertEqual(salida.traspaso.contraparte, salida)
        with self.assertRaisesMessage(ValidationError, "Saldo insuficiente"):
            servicios.registrar_traspaso(self.contadora, origen=caja, destino=banco, fecha=HOY, valor=D("21"), concepto="x")
        # Se anula entero: los dos lados.
        servicios.anular_movimiento(self.contadora, salida.traspaso, "Papeleta equivocada")
        self.assertEqual((servicios.saldo(caja), servicios.saldo(banco)), (D("100"), D("0")))
        self.assertTrue(MovimientoFinanciero.objects.get(pk=salida.pk).anulado)

    def test_traspaso_entre_empresas_no(self):
        caja_tyt = self.caja(empresa=self.tyt)
        with self.assertRaisesMessage(ValidationError, "misma empresa"):
            servicios.registrar_traspaso(self.contadora, origen=self.caja(), destino=caja_tyt, fecha=HOY, valor=D("1"),
                                         concepto="x")

    def test_anular_solo_un_lado_del_traspaso_lo_impide_la_base(self):
        caja = self.caja()
        banco = self.caja(saldo=D("0"), nombre="Pichincha", tipo=CuentaFinanciera.Tipo.CORRIENTE)
        salida = servicios.registrar_traspaso(self.contadora, origen=caja, destino=banco, fecha=HOY, valor=D("10"), concepto="x")
        with self.assertRaisesMessage(DatabaseError, "dos lados"), transaction.atomic():
            servicios._anular_uno(salida, self.contadora, "solo uno")
            with transaction.get_connection().cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def test_conciliar(self):
        banco = self.caja(saldo=D("0"), nombre="Pichincha", tipo=CuentaFinanciera.Tipo.CORRIENTE)
        m = self.mov(banco, Tipo.DEPOSITO, "25", referencia="DEP-9")
        servicios.conciliar(m, True)
        m.refresh_from_db()
        self.assertTrue(m.conciliado)
        self.assertEqual(m.conciliado_en, timezone.localdate())
        with self.assertRaisesMessage(ValidationError, "conciliado"):
            servicios.anular_movimiento(self.contadora, m, "x")
        servicios.conciliar(m, False)
        servicios.anular_movimiento(self.contadora, m, "x")

    def test_cliente_de_otra_empresa_no(self):
        cliente_tyt = self.cliente(empresa=self.tyt)
        with self.assertRaisesMessage(ValidationError, "otra empresa"):
            self.mov(self.caja(), Tipo.INGRESO, "5", cliente=cliente_tyt)

    def test_saldo_inicial_fijo_cuando_hay_movimientos(self):
        caja = self.caja()
        self.mov(caja, Tipo.INGRESO, "1")
        with self.assertRaisesMessage(DatabaseError, "ya tiene movimientos"), transaction.atomic():
            CuentaFinanciera.objects.filter(pk=caja.pk).update(saldo_inicial=D("5"))

    def test_caja_ligada_a_cuenta_de_grupo_no(self):
        with self.assertRaisesMessage(DatabaseError, "cuenta de movimiento del activo"), transaction.atomic():
            CuentaFinanciera.objects.create(empresa=self.multivisas, nombre="X", cuenta_contable=self.c["1.1.01"],
                                            fecha_saldo_inicial=HOY)


# --------------------------------------------------------------------------- libro diario
class LibroDiarioTests(BaseContabilidad):
    def test_numeracion_correlativa_por_empresa(self):
        a1 = servicios.contabilizar(self.asiento(), self.contadora)
        a2 = servicios.contabilizar(self.asiento(), self.contadora)
        b1 = servicios.contabilizar(self.asiento(empresa=self.tyt), self.contadora)
        self.assertEqual((a1.numero, a2.numero, b1.numero), (1, 2, 1))
        borrador = self.asiento()
        self.assertIsNone(borrador.numero)

    def test_no_contabiliza_si_no_cuadra(self):
        a = self.asiento(lineas=(("1.1.01.01", "50", "0"), ("4.1.01.02", "0", "40")))
        with self.assertRaisesMessage(ValidationError, "no cuadra"):
            servicios.contabilizar(a, self.contadora)
        # Ni saltándose la aplicación.
        with self.assertRaisesMessage(DatabaseError, "no cuadra"), transaction.atomic():
            Asiento.objects.filter(pk=a.pk).update(estado="CONTABILIZADO", numero=99, contabilizado_en=timezone.now())

    def test_no_contabiliza_con_cuenta_de_grupo(self):
        a = self.asiento(lineas=(("1.1.01", "50", "0"), ("4.1.01.02", "0", "50")))
        with self.assertRaisesMessage(ValidationError, "no admite movimientos"):
            servicios.contabilizar(a, self.contadora)

    def test_linea_va_al_debe_o_al_haber(self):
        with self.assertRaises(ValidationError):
            self.asiento(lineas=(("1.1.01.01", "50", "50"), ("4.1.01.02", "0", "0")))

    def test_contabilizado_no_cambia_y_se_anula(self):
        a = servicios.contabilizar(self.asiento(), self.contadora)
        with self.assertRaisesMessage(DatabaseError, "solo se puede anular"), transaction.atomic():
            Asiento.objects.filter(pk=a.pk).update(concepto="Otro")
        with self.assertRaisesMessage(DatabaseError, "borrador"), transaction.atomic():
            LineaAsiento.objects.filter(asiento=a).update(debe=D("1"))
        with self.assertRaisesMessage(ValidationError, "contabilizado"):
            servicios.guardar_borrador(a, [], self.contadora)
        with self.assertRaisesMessage(ValidationError, "Solo se borran"):
            servicios.borrar_borrador(a)
        servicios.anular_asiento(a, self.contadora, "Registrado dos veces")
        a.refresh_from_db()
        self.assertEqual((a.estado, a.numero), ("ANULADO", 1))

    def test_borrador_se_corrige_y_se_borra(self):
        a = self.asiento()
        servicios.guardar_borrador(a, [{"cuenta": self.c["1.1.01.01"], "debe": D("70"), "haber": D("0")},
                                       {"cuenta": self.c["4.1.01.02"], "debe": D("0"), "haber": D("70")}], self.contadora)
        self.assertEqual(servicios.totales(a), (D("70"), D("70")))
        servicios.borrar_borrador(a)
        self.assertFalse(Asiento.objects.filter(pk=a.pk).exists())

    def test_una_sola_apertura_por_empresa(self):
        self.asiento(tipo=Asiento.Tipo.APERTURA)
        with self.assertRaisesMessage(ValidationError, "ya tiene su asiento de apertura"):
            self.asiento(tipo=Asiento.Tipo.APERTURA)
        self.asiento(empresa=self.tyt, tipo=Asiento.Tipo.APERTURA)  # la otra empresa tiene la suya

    def test_balance_por_empresa_con_grupos(self):
        servicios.contabilizar(self.asiento(tipo=Asiento.Tipo.APERTURA, fecha=HOY - timedelta(days=10), lineas=(
            ("1.1.01.01", "500", "0"), ("1.1.02.01", "1500", "0"), ("3.1.01", "0", "2000"))), self.contadora)
        servicios.contabilizar(self.asiento(lineas=(("1.1.02.01", "300", "0"), ("4.1.01.02", "0", "300"))), self.contadora)
        servicios.contabilizar(self.asiento(empresa=self.tyt, lineas=(("1.1.01.01", "9", "0"), ("4.2.02", "0", "9"))),
                               self.contadora)
        anulado = servicios.contabilizar(self.asiento(), self.contadora)
        servicios.anular_asiento(anulado, self.contadora, "x")
        self.asiento(lineas=(("1.1.01.01", "1000", "0"), ("4.1.01.02", "0", "1000")))  # borrador: no cuenta

        filas, debe, haber = servicios.balance_comprobacion(self.multivisas)
        por_codigo = {f.cuenta.codigo: f for f in filas}
        self.assertEqual((debe, haber), (D("2300"), D("2300")))
        self.assertEqual(por_codigo["1.1.02.01"].saldo_deudor, D("1800"))
        self.assertEqual(por_codigo["1"].debe, D("2300"))  # el grupo suma sus subcuentas
        self.assertEqual(por_codigo["1.1.01"].saldo_deudor, D("500"))
        self.assertEqual(por_codigo["3.1.01"].saldo_acreedor, D("2000"))
        self.assertNotIn("4.2.02", por_codigo)  # es de T&T
        # Rango de fechas: sin la apertura.
        _, debe, _ = servicios.balance_comprobacion(self.multivisas, desde=HOY - timedelta(days=1))
        self.assertEqual(debe, D("300"))

    def test_libro_mayor_segun_naturaleza(self):
        servicios.contabilizar(self.asiento(lineas=(("1.1.01.01", "50", "0"), ("4.1.01.02", "0", "50"))), self.contadora)
        servicios.contabilizar(self.asiento(lineas=(("6.1.07", "20", "0"), ("1.1.01.01", "0", "20"))), self.contadora)
        _, filas, final = servicios.libro_mayor(self.multivisas, self.c["1.1.01"], None, None)
        self.assertEqual(([s for _, s in filas], final), ([D("50"), D("30")], D("30")))
        _, _, ingreso = servicios.libro_mayor(self.multivisas, self.c["4.1.01.02"], None, None)
        self.assertEqual(ingreso, D("50"))  # acreedora: el haber suma


# --------------------------------------------------------------------------- leads y clientes
class CrmTests(BaseContabilidad):
    def lead(self, **extra):
        return Lead.objects.create(empresa=self.multivisas, nombres="Luis Mora", telefono="0991234567", **extra)

    def test_convertir_lead_en_cliente(self):
        lead = self.lead(interes=Lead.Interes.PAQUETE)
        cliente = servicios.convertir_lead(lead, Cliente(nombres="Luis Mora", identificacion="1710034065",
                                                         linea="PAQUETES"), self.contadora)
        lead.refresh_from_db()
        self.assertEqual((lead.estado, lead.cliente, cliente.codigo, cliente.empresa), ("CONVERTIDO", cliente, "CLI-0001",
                                                                                         self.multivisas))
        self.assertEqual(lead.seguimientos.get().estado, "CONVERTIDO")
        with self.assertRaisesMessage(ValidationError, "cerrado"):
            servicios.convertir_lead(lead, Cliente(nombres="x", identificacion="0102030405"), self.contadora)

    def test_seguimiento_y_perdida_con_motivo(self):
        lead = self.lead()
        servicios.registrar_seguimiento(lead, self.contadora, "Se envió requisitos", Lead.Estado.CONTACTADO,
                                        HOY + timedelta(days=2))
        self.assertEqual((lead.estado, lead.proximo_contacto), ("CONTACTADO", HOY + timedelta(days=2)))
        with self.assertRaises(ValidationError):
            servicios.registrar_seguimiento(lead, self.contadora, "No contesta", Lead.Estado.PERDIDO)
        servicios.registrar_seguimiento(lead, self.contadora, "No contesta", Lead.Estado.PERDIDO, motivo_perdida="Sin respuesta")
        self.assertEqual((lead.estado, lead.proximo_contacto), ("PERDIDO", None))
        with self.assertRaisesMessage(DatabaseError, "no se modifica"), transaction.atomic():
            lead.seguimientos.update(nota="otra")

    def test_codigos_e_identificacion_por_empresa(self):
        a = self.cliente()
        b = self.cliente(empresa=self.tyt)  # la misma persona puede ser cliente de las dos empresas
        self.assertEqual((a.codigo, b.codigo), ("CLI-0001", "CLI-0001"))
        with self.assertRaisesMessage(ValidationError, "identificación"):
            self.cliente()
        p = servicios.guardar_con_codigo(Proveedor(empresa=self.multivisas, nombre="Avianca"), "PRV")
        self.assertEqual(p.codigo, "PRV-0001")

    def test_ruc(self):
        self.assertTrue(ruc_valido("1710034065001"))  # persona natural
        self.assertTrue(ruc_valido("1790016919001"))  # sociedad (tercer dígito 9)
        self.assertTrue(ruc_valido("1760001550001"))  # entidad pública (tercer dígito 6)
        self.assertFalse(ruc_valido("1710034065000"))
        self.assertFalse(ruc_valido("9910034065001"))
        self.assertFalse(ruc_valido("17100340650"))


# --------------------------------------------------------------------------- pantallas
class PantallasTests(BaseContabilidad):
    def setUp(self):
        self.client.force_login(self.contadora)

    def test_todas_las_listas_abren(self):
        caja = self.caja()
        self.mov(caja, Tipo.INGRESO, "5")
        asiento = self.asiento()
        cliente = self.cliente()
        lead = Lead.objects.create(empresa=self.multivisas, nombres="Luis", correo="l@x.ec")
        proveedor = servicios.guardar_con_codigo(Proveedor(empresa=self.multivisas, nombre="Avianca"), "PRV")
        rutas = [reverse(n) for n in (
            "contabilidad:inicio", "contabilidad:cajas", "contabilidad:movimientos", "contabilidad:movimiento_crear",
            "contabilidad:traspaso_crear", "contabilidad:plan_cuentas", "contabilidad:cuenta_crear", "contabilidad:diario",
            "contabilidad:asiento_crear", "contabilidad:saldos_iniciales", "contabilidad:balance", "contabilidad:activos",
            "contabilidad:activo_crear", "contabilidad:caja_crear", "crm:leads", "crm:lead_crear", "crm:clientes",
            "crm:cliente_crear", "crm:proveedores", "crm:proveedor_crear")]
        rutas += [reverse("contabilidad:caja_detalle", args=[caja.pk]), reverse("contabilidad:asiento_detalle", args=[asiento.pk]),
                  reverse("contabilidad:asiento_editar", args=[asiento.pk]), reverse("crm:cliente_detalle", args=[cliente.pk]),
                  reverse("crm:lead_detalle", args=[lead.pk]), reverse("crm:lead_convertir", args=[lead.pk]),
                  reverse("crm:proveedor_detalle", args=[proveedor.pk]), reverse("contabilidad:mayor", args=[self.c["1.1.01"].pk]),
                  reverse("contabilidad:asiento_crear") + "?tipo=APERTURA"]
        for ruta in rutas:
            self.assertEqual(self.client.get(ruta).status_code, 200, ruta)

    def test_cada_empresa_ve_lo_suyo(self):
        caja_tyt = self.caja(empresa=self.tyt, nombre="Caja T&T")
        self.assertEqual(self.client.get(reverse("contabilidad:caja_detalle", args=[caja_tyt.pk])).status_code, 404)
        self.client.post(reverse("contabilidad:cambiar_empresa"), {"empresa": self.tyt.pk})
        self.assertEqual(self.client.session[CLAVE_SESION], self.tyt.pk)
        self.assertEqual(self.client.get(reverse("contabilidad:caja_detalle", args=[caja_tyt.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("contabilidad:cajas")), "Caja T&amp;T")

    def test_registrar_y_contabilizar_asiento_desde_la_pantalla(self):
        cliente = self.cliente()
        datos = {"fecha": HOY.isoformat(), "tipo": "DIARIO", "concepto": "Cobro trámite visa", "documento": "FAC-001",
                 "lineas-TOTAL_FORMS": "3", "lineas-INITIAL_FORMS": "0", "lineas-MIN_NUM_FORMS": "2", "lineas-MAX_NUM_FORMS": "200",
                 "lineas-0-cuenta": self.c["1.1.01.01"].pk, "lineas-0-debe": "120", "lineas-0-tercero": f"c:{cliente.pk}",
                 "lineas-1-cuenta": self.c["4.1.01.02"].pk, "lineas-1-haber": "120",
                 "accion": "contabilizar"}  # la tercera línea va vacía: se ignora
        r = self.client.post(reverse("contabilidad:asiento_crear"), datos)
        asiento = Asiento.objects.get()
        self.assertRedirects(r, reverse("contabilidad:asiento_detalle", args=[asiento.pk]))
        self.assertEqual((asiento.estado, asiento.numero, asiento.lineas.count()), ("CONTABILIZADO", 1, 2))
        self.assertEqual(asiento.lineas.get(debe=D("120")).cliente, cliente)

    def test_asiento_descuadrado_queda_en_borrador(self):
        datos = {"fecha": HOY.isoformat(), "tipo": "DIARIO", "concepto": "x", "lineas-TOTAL_FORMS": "2",
                 "lineas-INITIAL_FORMS": "0", "lineas-MIN_NUM_FORMS": "2", "lineas-MAX_NUM_FORMS": "200",
                 "lineas-0-cuenta": self.c["1.1.01.01"].pk, "lineas-0-debe": "120",
                 "lineas-1-cuenta": self.c["4.1.01.02"].pk, "lineas-1-haber": "100", "accion": "contabilizar"}
        self.client.post(reverse("contabilidad:asiento_crear"), datos)
        self.assertEqual(Asiento.objects.get().estado, "BORRADOR")

    def test_movimiento_con_soporte_desde_la_pantalla(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        caja = self.caja()
        with tempfile.TemporaryDirectory() as carpeta, self.settings(MEDIA_ROOT=carpeta):
            r = self.client.post(reverse("contabilidad:movimiento_crear"), {
                "cuenta": caja.pk, "tipo": Tipo.INGRESO, "fecha": HOY.isoformat(), "valor": "40", "concepto": "Abono visa",
                "soporte": SimpleUploadedFile("recibo.pdf", b"%PDF-1.4 prueba", content_type="application/pdf")})
            self.assertRedirects(r, reverse("contabilidad:caja_detalle", args=[caja.pk]))
            m = caja.movimientos.get()
            self.assertEqual(m.soporte_mime, "application/pdf")
            self.assertEqual(self.client.get(reverse("contabilidad:movimiento_soporte", args=[m.pk])).status_code, 200)

    def test_convertir_lead_desde_la_pantalla(self):
        lead = Lead.objects.create(empresa=self.multivisas, nombres="Rosa Vera", telefono="0991112223")
        r = self.client.post(reverse("crm:lead_convertir", args=[lead.pk]), {
            "linea": "VISAS", "nombres": "Rosa Vera", "tipo_identificacion": "CEDULA", "identificacion": "1710034065",
            "telefono": "0991112223"})
        cliente = Cliente.objects.get()
        self.assertRedirects(r, reverse("crm:cliente_detalle", args=[cliente.pk]))
        lead.refresh_from_db()
        self.assertEqual(lead.cliente, cliente)


# --------------------------------------------------------------------------- activos
class ActivosTests(BaseContabilidad):
    def test_depreciacion_linea_recta(self):
        activo = ActivoFijo(empresa=self.multivisas, codigo="AF-1", descripcion="Laptop", cantidad=2,
                            valor_unitario=D("300"), fecha_compra=date(2025, 1, 15), vida_util_anios=3)
        self.assertEqual(activo.costo, D("600"))
        self.assertEqual(activo.depreciacion_acumulada(date(2026, 1, 15)), D("200"))  # 12 de 36 meses
        self.assertEqual(activo.valor_libros(date(2030, 1, 1)), D("0"))  # no pasa de la vida útil
        self.assertIsNone(ActivoFijo(valor_unitario=D("1")).depreciacion_acumulada())  # sin fecha ni vida útil

    def test_importar_inventario_del_excel(self):
        import openpyxl

        libro = openpyxl.Workbook()
        hoja = libro.active
        hoja.title = "INVENTARIO"
        for fila in [("INVENTARIO DE LA EMPRESA",), ("N", "Descripción", "Cantidad", "Valor unitario ($)", "Valor total ($)"),
                     (1, "Escritorios de oficina", 7, 100, "=D5*C5"), (10, "Cámara de seguridad", None, None, None),
                     (None, "TV 55", 1, 500, None), ("TOTAL", None, None, None, "=SUM(E5:E18)")]:
            hoja.append(fila)
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "inventario.xlsx"
            libro.save(ruta)
            call_command("importar_activos_excel", str(ruta), empresa="multivisas", verbosity=0, stdout=open("/dev/null", "w"))
            call_command("importar_activos_excel", str(ruta), empresa="MULTIVISAS", stdout=open("/dev/null", "w"))
        activos = {a.descripcion: a for a in ActivoFijo.objects.filter(empresa=self.multivisas)}
        self.assertEqual(set(activos), {"Escritorios de oficina", "Cámara de seguridad", "TV 55"})
        self.assertEqual((activos["Escritorios de oficina"].costo, activos["Cámara de seguridad"].cantidad), (D("700"), 1))


# --------------------------------------------------------------------------- estados financieros y mayor
class EstadosFinancierosTests(BaseContabilidad):
    def contabilizar(self, *lineas, empresa=None, fecha=HOY, tipo=Asiento.Tipo.DIARIO):
        return servicios.contabilizar(self.asiento(empresa=empresa, fecha=fecha, tipo=tipo, lineas=lineas), self.contadora)

    def movimientos_del_mes(self):
        # Apertura: caja 500, bancos 1500 y equipos 1000, contra capital 3000.
        self.contabilizar(("1.1.01.01", "500", "0"), ("1.1.02.01", "1500", "0"), ("1.2.01.01", "1000", "0"),
                          ("3.1.01", "0", "3000"), fecha=HOY - timedelta(days=40), tipo=Asiento.Tipo.APERTURA)
        self.contabilizar(("1.1.02.01", "800", "0"), ("4.1.01.02", "0", "800"))      # cobro de trámites
        self.contabilizar(("5.1.01", "200", "0"), ("1.1.02.01", "0", "200"))         # honorarios externos (costo)
        self.contabilizar(("6.1.06", "150", "0"), ("1.1.01.01", "0", "150"))         # arriendo (gasto)
        self.contabilizar(("6.1.11", "50", "0"), ("1.2.02.01", "0", "50"))           # depreciación del mes
        self.contabilizar(("1.1.01.01", "99", "0"), ("4.2.02", "0", "99"), empresa=self.tyt)  # otra empresa
        self.asiento(lineas=(("1.1.01.01", "1000", "0"), ("4.1.01.02", "0", "1000")))  # borrador: no cuenta

    def test_estado_de_resultados(self):
        self.movimientos_del_mes()
        e = servicios.estado_resultados(self.multivisas)
        self.assertEqual((e["ingresos"].total, e["costos"].total, e["gastos"].total), (D("800"), D("200"), D("200")))
        self.assertEqual((e["utilidad_bruta"], e["resultado"]), (D("600"), D("400")))
        self.assertIn("4.1", [f.cuenta.codigo for f in e["ingresos"].filas])  # con sus grupos
        # Solo el período: la apertura de hace 40 días no tiene resultados, el mes sí.
        self.assertEqual(servicios.estado_resultados(self.multivisas, hasta=HOY - timedelta(days=1))["resultado"], D("0"))
        self.assertEqual(servicios.estado_resultados(self.tyt)["resultado"], D("99"))

    def test_estado_de_situacion_cuadra_con_el_resultado_sin_cerrar(self):
        self.movimientos_del_mes()
        e = servicios.estado_situacion(self.multivisas, HOY)
        # Activo: caja 350 + bancos 2100 + equipos 1000 − depreciación 50.
        self.assertEqual(e["activo"].total, D("3400"))
        self.assertEqual(e["pasivo"].total, D("0"))
        self.assertEqual((e["patrimonio"].total, e["resultado"], e["total_patrimonio"]), (D("3000"), D("400"), D("3400")))
        self.assertTrue(e["cuadra"])
        depreciacion = next(f for f in e["activo"].filas if f.cuenta.codigo == "1.2.02.01")
        self.assertEqual(depreciacion.valor, D("-50"))  # correctora: resta del activo
        # A la fecha de la apertura, solo la apertura.
        antes = servicios.estado_situacion(self.multivisas, HOY - timedelta(days=30))
        self.assertEqual((antes["activo"].total, antes["resultado"], antes["cuadra"]), (D("3000"), D("0"), True))

    def test_pantallas(self):
        self.movimientos_del_mes()
        self.client.force_login(self.contadora)
        respuesta = self.client.get(reverse("contabilidad:estado_resultados"))
        self.assertContains(respuesta, "Utilidad del período")
        self.assertContains(respuesta, "400,00")
        self.assertEqual(self.client.get(reverse("contabilidad:estado_resultados") + "?nivel=1").status_code, 200)
        respuesta = self.client.get(reverse("contabilidad:estado_situacion"))
        self.assertContains(respuesta, "Cuadra: activo = pasivo + patrimonio")
        self.assertContains(respuesta, "3.400,00")
        respuesta = self.client.get(reverse("contabilidad:mayor_inicio"))
        con_movimiento = {f.cuenta.codigo for f, _ in respuesta.context["con_movimiento"]}
        self.assertIn("6.1.06", con_movimiento)
        self.assertNotIn("4.2.02", con_movimiento)  # movimiento de T&T
        cuenta = self.c["1.1.02.01"]
        self.assertRedirects(self.client.get(reverse("contabilidad:mayor_inicio"), {"cuenta": cuenta.pk, "desde": "2026-01-01"}),
                             reverse("contabilidad:mayor", args=[cuenta.pk]) + "?desde=2026-01-01", fetch_redirect_response=False)

    def test_el_vendedor_no_ve_los_estados(self):
        self.client.force_login(self.vendedor)
        for nombre in ("contabilidad:estado_resultados", "contabilidad:estado_situacion", "contabilidad:mayor_inicio"):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)
