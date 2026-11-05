"""Registro con aprobación, inicio de sesión por rol y dashboard."""
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Notificacion, Rol, Usuario

CLAVE = "Clave-Segura-2026"


def datos_registro(**cambios):
    datos = {
        "first_name": "ana  lucía", "last_name": "TORRES", "email": "Ana@Correo.com", "rol": Rol.VENDEDOR,
        "username": "atorres", "password1": CLAVE, "password2": CLAVE,
    }
    datos.update(cambios)
    return datos


class RegistroTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)

    def test_primer_registro_es_administrador_activo(self):
        respuesta = self.client.post(reverse("registro"), datos_registro(), follow=True)
        usuario = Usuario.objects.get(username="atorres")
        self.assertEqual(usuario.rol, Rol.ADMINISTRADOR)
        self.assertTrue(usuario.is_active)
        self.assertIsNotNone(usuario.aprobado_en)
        self.assertTrue(usuario.has_perm("personal.add_empleado"))
        self.assertContains(respuesta, "quedó como Administrador")
        self.assertEqual(usuario.first_name, "Ana Lucía")
        self.assertEqual(usuario.email, "ana@correo.com")

    def test_siguientes_registros_quedan_pendientes_y_se_notifica_al_admin(self):
        admin = Usuario.objects.create_user("admin", "admin@x.test", CLAVE, rol=Rol.ADMINISTRADOR, is_active=True)
        respuesta = self.client.post(reverse("registro"), datos_registro(rol=Rol.ENCARGADO_CIERRE))
        self.assertContains(respuesta, "pendiente de aprobación")
        usuario = Usuario.objects.get(username="atorres")
        self.assertFalse(usuario.is_active)
        self.assertTrue(usuario.pendiente_aprobacion)
        self.assertEqual(usuario.rol, Rol.ENCARGADO_CIERRE)
        self.assertTrue(Notificacion.objects.filter(destinatario=admin, titulo__contains="Ana Lucía Torres").exists())

    def test_no_se_puede_solicitar_rol_administrador(self):
        Usuario.objects.create_user("admin", "admin@x.test", CLAVE, rol=Rol.ADMINISTRADOR)
        respuesta = self.client.post(reverse("registro"), datos_registro(rol=Rol.ADMINISTRADOR))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("rol", respuesta.context["form"].errors)
        self.assertFalse(Usuario.objects.filter(username="atorres").exists())

    def test_validaciones(self):
        Usuario.objects.create_user("atorres", "otra@x.test", CLAVE, rol=Rol.ADMINISTRADOR)
        casos = {
            "username": {"username": "ATORRES"},  # ya existe (sin distinguir mayúsculas)
            "email": {"email": "otra@x.test"},
            "password2": {"password2": "distinta-123456"},
            "first_name": {"first_name": "Ana123"},
        }
        for campo, cambio in casos.items():
            with self.subTest(campo=campo):
                datos = datos_registro(**cambio)
                if campo != "username":
                    datos["username"] = "nuevo_usuario"
                form = self.client.post(reverse("registro"), datos).context["form"]
                self.assertIn(campo, form.errors)
        respuesta = self.client.post(reverse("registro"), datos_registro(username="x2", password1="1234567890", password2="1234567890"))
        self.assertIn("password2", respuesta.context["form"].errors)


class IngresoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin", "admin@x.test", CLAVE, rol=Rol.ADMINISTRADOR)
        cls.pendiente = Usuario.objects.create_user("pend", "pend@x.test", CLAVE, rol=Rol.VENDEDOR, is_active=False)

    def test_ingreso_correcto_lleva_al_dashboard(self):
        respuesta = self.client.post(reverse("login"), {"username": "admin", "password": CLAVE})
        self.assertRedirects(respuesta, reverse("inicio"))

    def test_cuenta_pendiente_recibe_mensaje_claro(self):
        respuesta = self.client.post(reverse("login"), {"username": "pend", "password": CLAVE})
        self.assertContains(respuesta, "pendiente de aprobación")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_clave_incorrecta_no_revela_estado_de_la_cuenta(self):
        respuesta = self.client.post(reverse("login"), {"username": "pend", "password": "otra-clave-x"})
        self.assertContains(respuesta, "Usuario o contraseña incorrectos")
        self.assertNotContains(respuesta, "pendiente de aprobación")

    def test_dashboard_requiere_sesion(self):
        self.assertRedirects(self.client.get(reverse("inicio")), f"{reverse('login')}?next=/")


class AprobacionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin", "admin@x.test", CLAVE, rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vend", "vend@x.test", CLAVE, rol=Rol.VENDEDOR)

    def setUp(self):
        self.pendiente = Usuario.objects.create_user(
            "pend", "pend@x.test", CLAVE, first_name="Pedro", last_name="Paz", rol=Rol.VENDEDOR, is_active=False)

    def test_admin_aprueba_cambiando_el_rol(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("aprobar_solicitud", args=[self.pendiente.pk]),
                                     {"rol": Rol.ENCARGADO_CIERRE}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Se aprobó la cuenta de Pedro Paz como Encargado de cierre")
        self.pendiente.refresh_from_db()
        self.assertTrue(self.pendiente.is_active)
        self.assertEqual(self.pendiente.aprobado_por, self.admin)
        self.assertTrue(self.pendiente.has_perm("cierre.add_gestioncierre"))
        self.assertTrue(self.client.login(username="pend", password=CLAVE))

    def test_admin_rechaza(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("rechazar_solicitud", args=[self.pendiente.pk]))
        self.assertFalse(Usuario.objects.filter(pk=self.pendiente.pk).exists())

    def test_no_admin_no_puede_aprobar(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.post(reverse("aprobar_solicitud", args=[self.pendiente.pk]), {"rol": Rol.ADMINISTRADOR}).status_code, 403)
        self.assertEqual(self.client.get(reverse("solicitudes_acceso")).status_code, 403)
        self.pendiente.refresh_from_db()
        self.assertFalse(self.pendiente.is_active)

    def test_no_se_puede_aprobar_una_cuenta_ya_activa(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("aprobar_solicitud", args=[self.vendedor.pk]), {"rol": Rol.ADMINISTRADOR}).status_code, 404)


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin", "admin@x.test", CLAVE, first_name="Gabo", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vend", "vend@x.test", CLAVE, rol=Rol.VENDEDOR)
        Usuario.objects.create_user("pend", "pend@x.test", CLAVE, first_name="Pedro", last_name="Paz", is_active=False)

    def test_admin_ve_todos_sus_modulos_indicadores_y_solicitudes(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("inicio"))
        for texto in ["Gabo", "Empleados activos", "Solicitudes de acceso", "Materiales con stock bajo",
                      "Personal", "Asistencia", "Inventario", "Nómina", "Cierre de ventas", "Reportes",
                      "Pedro Paz", reverse("personal:empleado_lista")]:
            self.assertContains(respuesta, texto)
        self.assertContains(respuesta, "Próximamente")

    def test_vendedor_ve_solo_sus_modulos(self):
        self.client.force_login(self.vendedor)
        respuesta = self.client.get(reverse("inicio"))
        self.assertContains(respuesta, "Ventas")
        self.assertContains(respuesta, "Agenda")
        for texto in ["Empleados activos", "Solicitudes de acceso", "Nómina", "Inventario", "Pedro Paz",
                      reverse("personal:empleado_lista"), reverse("solicitudes_acceso")]:
            self.assertNotContains(respuesta, texto)

    def test_marcar_notificaciones_leidas(self):
        Notificacion.objects.create(destinatario=self.vendedor, titulo="Hola")
        self.client.force_login(self.vendedor)
        respuesta = self.client.post(reverse("notificaciones_leidas"), HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Hola")
        self.assertFalse(Notificacion.objects.filter(destinatario=self.vendedor, leida=False).exists())
