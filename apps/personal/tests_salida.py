"""
Salida de empleados (despedido / renunció) sin borrar su registro.

  1. Se registra la salida con motivo y fecha; el empleado NO se borra y su historial se conserva.
  2. El ex empleado deja de verse en el personal actual, pero se puede consultar con el filtro «Ex empleados».
  3. Su cuenta de acceso se desactiva y no se puede reactivar sin reintegrarlo.
  4. Se puede reintegrar (volver a contratar).
  5. La base de datos exige el motivo cuando el empleado está desvinculado.
"""
from datetime import date, timedelta

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.core.cuentas import crear_cuenta_para_empleado
from apps.core.models import Rol, Usuario

from .models import Cargo, Empleado, ObservacionEmpleado
from .tests import CEDULA_VALIDA, OTRA_CEDULA_VALIDA


class SalidaEmpleadoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin1", "admin1@callflow.test", "clave-segura-123", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vend1", "vend1@callflow.test", "clave-segura-123", rol=Rol.VENDEDOR)
        cls.cargo = Cargo.objects.get(nombre="Asesor de ventas")

    def setUp(self):
        self.client.force_login(self.admin)
        self.empleado = Empleado.objects.create(
            identificacion=CEDULA_VALIDA, nombres="Luis", apellidos="Andrade", telefono="0991234567",
            cargo=self.cargo, fecha_ingreso=date(2026, 1, 15),
        )
        self.otro = Empleado.objects.create(identificacion=OTRA_CEDULA_VALIDA, nombres="Ana", apellidos="Ruiz",
                                            cargo=self.cargo, fecha_ingreso=date(2026, 1, 15))
        self.url_salida = reverse("personal:empleado_salida", args=[self.empleado.pk])
        self.url_ficha = reverse("personal:empleado_detalle", args=[self.empleado.pk])
        self.ayer = date.today() - timedelta(days=1)

    def registrar_salida(self, motivo="RENUNCIA", fecha=None, detalle=""):
        return self.client.post(self.url_salida, {"motivo": motivo, "fecha": (fecha or self.ayer).isoformat(), "detalle": detalle})

    # ----------------------------------------------------------- 1
    def test_registra_salida_sin_borrar_el_registro(self):
        ObservacionEmpleado.objects.create(empleado=self.empleado, texto="Buen trabajo", autor=self.admin)
        respuesta = self.registrar_salida("DESPIDO", detalle="Faltas reiteradas")
        self.assertRedirects(respuesta, self.url_ficha, fetch_redirect_response=False)
        self.empleado.refresh_from_db()
        self.assertEqual(self.empleado.estado, Empleado.Estado.DESVINCULADO)
        self.assertEqual(self.empleado.motivo_salida, Empleado.MotivoSalida.DESPIDO)
        self.assertEqual(self.empleado.fecha_salida, self.ayer)
        self.assertEqual(self.empleado.detalle_salida, "Faltas reiteradas")
        self.assertEqual(self.empleado.observaciones.count(), 1)  # su historial sigue ahí
        ficha = self.client.get(self.url_ficha)
        self.assertContains(ficha, "Ex empleado")
        self.assertContains(ficha, "Despedido")

    def test_valida_motivo_y_fecha(self):
        self.registrar_salida(motivo="")
        self.registrar_salida(fecha=date(2025, 12, 31))          # antes del ingreso
        self.registrar_salida(fecha=date.today() + timedelta(days=3))  # futura
        self.empleado.refresh_from_db()
        self.assertEqual(self.empleado.estado, Empleado.Estado.ACTIVO)

    def test_vendedor_no_puede_registrar_salidas(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.registrar_salida().status_code, 403)

    def test_corregir_la_salida(self):
        self.registrar_salida("OTRO")
        self.registrar_salida("FIN_CONTRATO", fecha=date(2026, 2, 1))
        self.empleado.refresh_from_db()
        self.assertEqual(self.empleado.motivo_salida, "FIN_CONTRATO")
        self.assertEqual(self.empleado.fecha_salida, date(2026, 2, 1))

    # ----------------------------------------------------------- 2
    def test_listado_oculta_ex_empleados_por_defecto(self):
        self.registrar_salida()
        lista = reverse("personal:empleado_lista")
        actuales = list(self.client.get(lista).context["empleados"])
        self.assertEqual(actuales, [self.otro])
        self.assertEqual(list(self.client.get(lista, {"estado": "EX"}).context["empleados"]), [self.empleado])
        self.assertEqual(len(self.client.get(lista, {"estado": "TODOS"}).context["empleados"]), 2)
        # Pedir el estado interno por URL no salta el filtro de ex empleados.
        self.assertEqual(list(self.client.get(lista, {"estado": "DESVINCULADO"}).context["empleados"]), [self.otro])

    def test_no_aparece_en_los_selectores_del_sistema(self):
        from apps.asistencia.servicios import tablero_del_dia

        self.registrar_salida()
        self.assertNotIn(self.empleado, [f.empleado for f in tablero_del_dia(date.today())])

    # ----------------------------------------------------------- 3
    def test_desactiva_la_cuenta_y_no_se_reactiva_desde_usuarios(self):
        usuario, _ = crear_cuenta_para_empleado(self.empleado, creado_por=self.admin)
        self.registrar_salida()
        usuario.refresh_from_db()
        self.assertFalse(usuario.is_active)

        usuarios = reverse("usuarios")
        self.assertNotIn(usuario, self.client.get(usuarios).context["usuarios"])  # por defecto solo activas
        self.assertIn(usuario, self.client.get(usuarios, {"estado": "INACTIVAS"}).context["usuarios"])

        self.client.post(reverse("usuario_cambiar_estado", args=[usuario.pk]))
        usuario.refresh_from_db()
        self.assertFalse(usuario.is_active)

    # ----------------------------------------------------------- 4
    def test_reintegrar(self):
        usuario, _ = crear_cuenta_para_empleado(self.empleado, creado_por=self.admin)
        self.registrar_salida()
        self.client.post(reverse("personal:empleado_reintegrar", args=[self.empleado.pk]))
        self.empleado.refresh_from_db()
        usuario.refresh_from_db()
        self.assertEqual(self.empleado.estado, Empleado.Estado.ACTIVO)
        self.assertIsNone(self.empleado.fecha_salida)
        self.assertEqual(self.empleado.motivo_salida, "")
        self.assertTrue(usuario.is_active)

    # ----------------------------------------------------------- 5
    def test_base_de_datos_exige_motivo(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Empleado.objects.filter(pk=self.empleado.pk).update(estado="DESVINCULADO", fecha_salida=self.ayer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Empleado.objects.filter(pk=self.empleado.pk).update(motivo_salida="DESPIDO")  # motivo sin salida

    # ----------------------------------------------------------- «Otro motivo»
    def test_otro_motivo_exige_escribir_cual(self):
        self.client.post(self.url_salida, {"motivo": "OTRO", "motivo_otro": "  ", "fecha": self.ayer.isoformat()})
        self.empleado.refresh_from_db()
        self.assertEqual(self.empleado.estado, Empleado.Estado.ACTIVO)

        self.client.post(self.url_salida, {"motivo": "OTRO", "motivo_otro": "Abandono  del trabajo", "fecha": self.ayer.isoformat()})
        self.empleado.refresh_from_db()
        self.assertEqual(self.empleado.motivo_otro, "Abandono del trabajo")
        self.assertEqual(self.empleado.motivo_salida_texto, "Abandono del trabajo")
        self.assertContains(self.client.get(self.url_ficha), "Abandono del trabajo")
        self.assertContains(self.client.get(reverse("personal:empleado_lista"), {"estado": "EX"}), "Abandono del trabajo")

    def test_el_texto_se_borra_si_el_motivo_ya_no_es_otro(self):
        self.client.post(self.url_salida, {"motivo": "OTRO", "motivo_otro": "Mutuo acuerdo", "fecha": self.ayer.isoformat()})
        self.client.post(self.url_salida, {"motivo": "RENUNCIA", "motivo_otro": "Mutuo acuerdo", "fecha": self.ayer.isoformat()})
        self.empleado.refresh_from_db()
        self.assertEqual((self.empleado.motivo_salida, self.empleado.motivo_otro), ("RENUNCIA", ""))
        self.assertEqual(self.empleado.motivo_salida_texto, "Renunció")

    def test_base_de_datos_exige_texto_con_otro_motivo(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Empleado.objects.filter(pk=self.empleado.pk).update(estado="DESVINCULADO", fecha_salida=self.ayer, motivo_salida="OTRO")
