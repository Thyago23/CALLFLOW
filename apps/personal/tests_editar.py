"""
SCRUM-13 · Actualizar datos de empleados.

Criterios de aceptación:
  1. Se pueden editar los datos personales y laborales de un empleado existente.
  2. Los cambios se validan igual que en el registro.
  3. Cada cambio importante queda en el registro de cambios (usuario, fecha y valor anterior).
"""
from datetime import date, timedelta

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core.cuentas import crear_cuenta_para_empleado
from apps.core.models import RegistroCambio, Rol, Usuario

from .models import Cargo, Empleado
from .tests import CEDULA_VALIDA, OTRA_CEDULA_VALIDA


class EditarEmpleadoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin1", "admin1@callflow.test", "clave-segura-123", rol=Rol.ADMINISTRADOR,
                                                first_name="Ana", last_name="Admin")
        cls.vendedor = Usuario.objects.create_user("vend1", "vend1@callflow.test", "clave-segura-123", rol=Rol.VENDEDOR)
        cls.cargo_vendedor = Cargo.objects.get(nombre="Asesor de ventas")
        cls.cargo_cierre = Cargo.objects.get(nombre="Supervisor")

    def setUp(self):
        self.client.force_login(self.admin)
        self.empleado = Empleado.objects.create(
            identificacion=CEDULA_VALIDA, nombres="Luis", apellidos="Andrade", telefono="0991234567",
            cargo=self.cargo_vendedor, fecha_ingreso=date(2026, 1, 15),
        )
        self.url = reverse("personal:empleado_editar", args=[self.empleado.pk])

    def datos(self, **cambios):
        e = self.empleado
        return {
            "tipo_identificacion": e.tipo_identificacion, "identificacion": e.identificacion, "nombres": e.nombres,
            "apellidos": e.apellidos, "fecha_nacimiento": "", "telefono": e.telefono, "email": e.email,
            "direccion": e.direccion, "cargo": e.cargo_id, "tipo_contrato": e.tipo_contrato,
            "fecha_ingreso": e.fecha_ingreso.isoformat(), "estado": e.estado, "fecha_salida": "",
        } | cambios

    # ----------------------------------------------------------- criterio 1
    def test_criterio1_edita_datos_personales_y_laborales(self):
        self.assertContains(self.client.get(self.url), "Guardar cambios")
        respuesta = self.client.post(self.url, self.datos(
            fecha_nacimiento="1998-03-14", direccion="Av. 6 de Diciembre  y Colón", email="Luis@Correo.com",
            cargo=self.cargo_cierre.pk, tipo_contrato="PLAZO_FIJO",
        ))
        self.assertRedirects(respuesta, reverse("personal:empleado_detalle", args=[self.empleado.pk]) + "#datos",
                             fetch_redirect_response=False)
        self.empleado.refresh_from_db()
        self.assertEqual(self.empleado.fecha_nacimiento, date(1998, 3, 14))
        self.assertEqual(self.empleado.direccion, "Av. 6 de Diciembre y Colón")  # se normaliza igual que al registrar
        self.assertEqual(self.empleado.email, "luis@correo.com")
        self.assertEqual(self.empleado.cargo, self.cargo_cierre)
        self.assertEqual(self.empleado.tipo_contrato, "PLAZO_FIJO")

    def test_sin_cambios_no_guarda_nada(self):
        antes = RegistroCambio.objects.count()
        self.client.post(self.url, self.datos())
        self.assertEqual(RegistroCambio.objects.count(), antes)

    def test_ficha_tiene_boton_editar_y_vendedor_no_puede(self):
        ficha = self.client.get(reverse("personal:empleado_detalle", args=[self.empleado.pk]))
        self.assertContains(ficha, self.url)
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.datos(nombres="Otro")).status_code, 403)

    # ----------------------------------------------------------- criterio 2
    def test_criterio2_mismas_validaciones_que_el_registro(self):
        casos = {"identificacion": "1234567890", "nombres": "L1s", "telefono": "abc", "fecha_nacimiento": "2999-01-01"}
        for campo, valor in casos.items():
            with self.subTest(campo=campo):
                respuesta = self.client.post(self.url, self.datos(**{campo: valor}))
                self.assertEqual(respuesta.status_code, 200)
                self.assertTrue(respuesta.context["form"].errors.get(campo))
        self.empleado.refresh_from_db()
        self.assertEqual(self.empleado.nombres, "Luis")

    def test_criterio2_no_permite_identificacion_de_otro_empleado(self):
        Empleado.objects.create(identificacion=OTRA_CEDULA_VALIDA, nombres="Ana", apellidos="Ruiz",
                                cargo=self.cargo_vendedor, fecha_ingreso=date.today())
        respuesta = self.client.post(self.url, self.datos(identificacion=OTRA_CEDULA_VALIDA))
        self.assertContains(respuesta, "Ya existe un empleado registrado con esta identificación")

    def test_verificacion_en_vivo_no_marca_la_propia_identificacion(self):
        url = reverse("personal:verificar_identificacion")
        consulta = {"tipo_identificacion": "CEDULA", "identificacion": CEDULA_VALIDA}
        self.assertContains(self.client.get(url, consulta), "Ya está registrada")
        self.assertContains(self.client.get(url, consulta | {"excluir": self.empleado.pk}), "disponible")

    def test_desvinculado_exige_fecha_de_salida_valida(self):
        respuesta = self.client.post(self.url, self.datos(estado="DESVINCULADO"))
        self.assertTrue(respuesta.context["form"].errors.get("fecha_salida"))
        respuesta = self.client.post(self.url, self.datos(estado="DESVINCULADO", fecha_salida="2025-12-31"))
        self.assertIn("anterior a la fecha de ingreso", str(respuesta.context["form"].errors["fecha_salida"]))

    # ----------------------------------------------------------- criterio 3
    def test_criterio3_cambio_queda_en_la_auditoria_con_usuario_y_valor_anterior(self):
        self.client.post(self.url, self.datos(telefono="0987654321", cargo=self.cargo_cierre.pk))
        cambio = RegistroCambio.objects.filter(
            tabla="personal_empleado", registro_id=str(self.empleado.pk), accion="UPDATE").latest("fecha")
        self.assertEqual(cambio.usuario_id, self.admin.pk)
        self.assertEqual(cambio.datos_anteriores["telefono"], "0991234567")
        self.assertEqual(cambio.datos_nuevos["telefono"], "0987654321")

        ficha = self.client.get(reverse("personal:empleado_detalle", args=[self.empleado.pk]))
        historial = ficha.context["historial_cambios"]
        self.assertEqual(historial[0]["usuario"], "Ana Admin (admin1)")
        cambios = {c["campo"]: (c["antes"], c["despues"]) for c in historial[0]["cambios"]}
        self.assertEqual(cambios["Teléfono"], ("0991234567", "0987654321"))
        self.assertEqual(cambios["Cargo"], ("Asesor de ventas", "Supervisor"))
        self.assertContains(ficha, "Historial de cambios")

    # ----------------------------------------------------------- cuenta de acceso vinculada
    def test_la_cuenta_se_actualiza_con_el_empleado(self):
        usuario, _ = crear_cuenta_para_empleado(self.empleado, creado_por=self.admin)
        self.client.post(self.url, self.datos(nombres="Luis Alberto", cargo=self.cargo_cierre.pk))
        usuario.refresh_from_db()
        self.assertEqual(usuario.first_name, "Luis Alberto")
        self.assertEqual(usuario.rol, Rol.ENCARGADO_CIERRE)
        self.assertEqual(usuario.username, "luis.andrade")  # el usuario de ingreso no cambia
        self.assertTrue(usuario.groups.filter(name="Supervisor").exists())

    def test_desvincular_desactiva_la_cuenta(self):
        usuario, _ = crear_cuenta_para_empleado(self.empleado, creado_por=self.admin)
        salida = (date.today() - timedelta(days=1)).isoformat()
        self.client.post(self.url, self.datos(estado="DESVINCULADO", fecha_salida=salida))
        usuario.refresh_from_db()
        self.assertFalse(usuario.is_active)
        self.empleado.refresh_from_db()
        self.assertEqual(self.empleado.estado, "DESVINCULADO")
