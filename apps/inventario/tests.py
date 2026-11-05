"""
Inventario: SCRUM-25 registrar materiales, SCRUM-26 existencias y movimientos,
SCRUM-27 notificaciones por stock bajo.
"""
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Notificacion, Rol, Usuario

from . import servicios
from .models import CategoriaMaterial, Material, MovimientoInventario

Tipo = MovimientoInventario.Tipo


class BaseInventario(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("gabo.admin", None, "x", rol=Rol.ADMINISTRADOR)
        cls.admin2 = Usuario.objects.create_user("diego.mendoza", None, "x", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vera.ventas", None, "x", rol=Rol.VENDEDOR)
        cls.papeleria = CategoriaMaterial.objects.get(nombre="Papelería")

    def material(self, nombre="Resma A4", minimo=5, inicial=D("20")):
        return servicios.crear_material(
            Material(nombre=nombre, categoria=self.papeleria, unidad_medida="RESMA", stock_minimo=minimo), inicial, self.admin)


class MaterialesTests(BaseInventario):
    """SCRUM-25"""

    def test_codigo_automatico_y_stock_inicial(self):
        resma = self.material()
        esferos = self.material("Esferos azules", inicial=None)
        self.assertEqual((resma.codigo, esferos.codigo), ("MAT-0001", "MAT-0002"))
        self.assertEqual(resma.stock_actual, 20)
        self.assertEqual(resma.movimientos.get().motivo, "Stock inicial")
        self.assertEqual(esferos.stock_actual, 0)
        self.assertFalse(esferos.movimientos.exists())

    def test_formulario_valida_nombre_repetido_y_categoria(self):
        self.material()
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("inventario:material_crear"), {
            "nombre": "  resma   a4 ", "unidad_medida": "RESMA", "stock_minimo": "1"})
        form = respuesta.context["form"]
        self.assertIn("Ya existe el material", form.errors["nombre"][0])
        self.assertIn("categoria", form.errors)

    def test_crear_desde_la_pantalla_con_categoria_nueva(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("inventario:material_crear"), {
            "nombre": "Tóner HP 85A", "codigo": "", "categoria": "", "categoria_nueva": "Insumos de impresión",
            "unidad_medida": "UNIDAD", "stock_minimo": "2", "stock_inicial": "6", "descripcion": ""}, follow=True)
        toner = Material.objects.get(nombre="Tóner HP 85A")
        self.assertRedirects(respuesta, reverse("inventario:material_detalle", args=[toner.pk]))
        self.assertEqual((toner.categoria.nombre, toner.stock_actual), ("Insumos de impresión", 6))
        self.assertContains(respuesta, "registrado con el código MAT-0001")

    def test_editar_no_cambia_el_stock(self):
        resma = self.material()
        self.client.force_login(self.admin)
        self.client.post(reverse("inventario:material_editar", args=[resma.pk]), {
            "nombre": "Resma A4 75 g", "codigo": resma.codigo, "categoria": self.papeleria.pk,
            "unidad_medida": "RESMA", "stock_minimo": "8", "stock_actual": "999", "descripcion": ""})
        resma.refresh_from_db()
        self.assertEqual((resma.nombre, resma.stock_minimo, resma.stock_actual), ("Resma A4 75 g", 8, 20))


class MovimientosTests(BaseInventario):
    """SCRUM-26"""

    def test_entradas_salidas_y_ajustes_actualizan_el_stock(self):
        resma = self.material()
        self.assertEqual(servicios.registrar_movimiento(resma, Tipo.SALIDA, D("7"), "Ventas", self.admin).stock_resultante, 13)
        self.assertEqual(servicios.registrar_movimiento(resma, Tipo.ENTRADA, D("10"), "Compra", self.admin).stock_resultante, 23)
        self.assertEqual(servicios.registrar_movimiento(resma, Tipo.AJUSTE_NEGATIVO, D("1"), "Resma mojada", self.admin).stock_resultante, 22)
        resma.refresh_from_db()
        self.assertEqual(resma.stock_actual, 22)
        self.assertEqual(resma.movimientos.count(), 4)

    def test_no_se_saca_mas_de_lo_que_hay(self):
        resma = self.material()
        with self.assertRaisesMessage(ValidationError, "Stock insuficiente"):
            servicios.registrar_movimiento(resma, Tipo.SALIDA, D("21"), "", self.admin)
        resma.refresh_from_db()
        self.assertEqual(resma.stock_actual, 20)

    def test_ajuste_exige_motivo_y_cantidad_positiva(self):
        resma = self.material()
        with self.assertRaisesMessage(ValidationError, "motivo"):
            servicios.registrar_movimiento(resma, Tipo.AJUSTE_POSITIVO, D("1"), " ", self.admin)
        with self.assertRaisesMessage(ValidationError, "mayor que cero"):
            servicios.registrar_movimiento(resma, Tipo.ENTRADA, D("0"), "", self.admin)

    def test_material_inactivo_no_admite_movimientos(self):
        resma = servicios.cambiar_estado(self.material())
        with self.assertRaisesMessage(ValidationError, "inactivo"):
            servicios.registrar_movimiento(resma, Tipo.ENTRADA, D("1"), "", self.admin)

    def test_los_movimientos_no_se_editan(self):
        resma = self.material()
        with self.assertRaises(DatabaseError), transaction.atomic():
            MovimientoInventario.objects.filter(material=resma).update(cantidad=1)

    def test_registrar_desde_la_pantalla(self):
        resma = self.material()
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("inventario:movimiento_crear"), {
            "material": resma.pk, "tipo": "SALIDA", "cantidad": "3", "motivo": "Entrega al área de ventas"}, follow=True)
        self.assertRedirects(respuesta, reverse("inventario:material_detalle", args=[resma.pk]))
        self.assertContains(respuesta, "Stock de «Resma A4»: 17")
        self.assertContains(respuesta, "Entrega al área de ventas")
        respuesta = self.client.post(reverse("inventario:movimiento_crear"), {
            "material": resma.pk, "tipo": "SALIDA", "cantidad": "100", "motivo": ""})
        self.assertContains(respuesta, "Stock insuficiente")

    def test_lista_de_movimientos_con_filtros(self):
        resma, esferos = self.material(), self.material("Esferos", inicial=D("50"))
        servicios.registrar_movimiento(esferos, Tipo.SALIDA, D("5"), "x", self.admin)
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("inventario:movimientos"), {"material": esferos.pk, "tipo": "SALIDA"})
        self.assertEqual([m.material for m in respuesta.context["movimientos"]], [esferos])


class StockBajoTests(BaseInventario):
    """SCRUM-27"""

    def test_al_llegar_al_minimo_se_notifica_a_todos_los_administradores(self):
        resma = self.material(minimo=5)
        servicios.registrar_movimiento(resma, Tipo.SALIDA, D("10"), "", self.admin)  # 20 -> 10: aún no
        self.assertFalse(Notificacion.objects.filter(tipo="STOCK_BAJO").exists())
        servicios.registrar_movimiento(resma, Tipo.SALIDA, D("6"), "", self.admin)   # 10 -> 4: cruza el mínimo
        avisos = Notificacion.objects.filter(tipo="STOCK_BAJO")
        self.assertEqual(set(avisos.values_list("destinatario__username", flat=True)), {"gabo.admin", "diego.mendoza"})
        self.assertEqual(avisos.first().url, f"/inventario/materiales/{resma.pk}/")
        servicios.registrar_movimiento(resma, Tipo.SALIDA, D("1"), "", self.admin)   # sigue bajo: no repite el aviso
        self.assertEqual(avisos.count(), 2)

    def test_lista_avisa_y_filtra_el_stock_bajo(self):
        resma = self.material(minimo=5)
        self.material("Esferos", minimo=5, inicial=D("50"))
        servicios.registrar_movimiento(resma, Tipo.SALIDA, D("17"), "", self.admin)
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("inventario:materiales"))
        self.assertContains(respuesta, "1 material en o bajo el stock mínimo")
        filtrado = self.client.get(reverse("inventario:materiales"), {"estado": "bajo"})
        self.assertEqual(list(filtrado.context["materiales"]), [resma])
        tablero = self.client.get(reverse("inicio"))
        self.assertContains(tablero, reverse("inventario:materiales") + "?estado=bajo")


class PermisosTests(BaseInventario):
    def test_vendedor_no_accede(self):
        resma = self.material()
        self.client.force_login(self.vendedor)
        for url in [reverse("inventario:materiales"), reverse("inventario:movimientos"),
                    reverse("inventario:material_detalle", args=[resma.pk]), reverse("inventario:movimiento_crear")]:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(reverse("inventario:movimiento_crear"),
                                          {"material": resma.pk, "tipo": "SALIDA", "cantidad": "1"}).status_code, 403)
