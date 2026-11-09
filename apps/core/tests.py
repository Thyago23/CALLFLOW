"""Usuarios creados por el administrador: usuario nombre.apellido + código de acceso aleatorio."""
import re
from datetime import date

from django.core.management import call_command
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.personal.models import Cargo, Empleado

from .codigos import base_usuario, generar_codigo_acceso, normalizar_usuario, siguiente_usuario
from .models import Notificacion, Rol, Usuario

CLAVE = "Clave-Segura-2026"


class GeneradoresTests(TestCase):
    def test_usuario_nombre_apellido(self):
        self.assertEqual(base_usuario("Lucía", "Mora"), "lucia.mora")
        self.assertEqual(base_usuario("María José", "Pérez Ñañez"), "maria.perez")
        self.assertEqual(base_usuario("Ángel", "Núñez"), "angel.nunez")

    def test_usuario_repetido_recibe_numero(self):
        self.assertEqual(siguiente_usuario("lucia.mora", []), "lucia.mora")
        self.assertEqual(siguiente_usuario("lucia.mora", ["lucia.mora"]), "lucia.mora2")
        self.assertEqual(siguiente_usuario("lucia.mora", ["lucia.mora", "lucia.mora2"]), "lucia.mora3")

    def test_normalizar_lo_que_se_escribe(self):
        for escrito in ["Lucía Mora", "  lucia   mora ", "LUCIA.MORA", "lucia_mora"]:
            self.assertEqual(normalizar_usuario(escrito), "lucia.mora")

    def test_codigo_de_acceso_iniciales_y_8_digitos_aleatorios(self):
        codigos = {generar_codigo_acceso("Lucía", "Mora") for _ in range(200)}
        self.assertTrue(all(re.fullmatch(r"LM\d{8}", c) for c in codigos))
        self.assertGreater(len(codigos), 195)  # aleatorios: prácticamente nunca se repiten
        self.assertTrue(re.fullmatch(r"AN\d{8}", generar_codigo_acceso("ángel", "ñañez")))


class CreacionUsuarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("gabo.admin", None, CLAVE, first_name="Gabo", last_name="Admin", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vera.ventas", None, CLAVE, rol=Rol.VENDEDOR)

    def crear(self, **datos):
        datos = {"first_name": "lucía", "last_name": "mora", "email": "", "rol": Rol.VENDEDOR, "empleado": ""} | datos
        return self.client.post(reverse("usuario_crear"), datos, follow=True)

    def test_registro_publico_no_existe(self):
        with self.assertRaises(NoReverseMatch):
            reverse("registro")
        self.assertEqual(self.client.get("/cuentas/registro/").status_code, 404)

    def test_admin_crea_usuario_y_recibe_credenciales_una_vez(self):
        self.client.force_login(self.admin)
        respuesta = self.crear()
        usuario = Usuario.objects.get(first_name="Lucía", last_name="Mora")
        codigo = respuesta.context["clave"]
        self.assertEqual(usuario.username, "lucia.mora")
        self.assertRegex(codigo, r"^LM\d{8}$")
        self.assertTrue(usuario.check_password(codigo))
        self.assertNotEqual(usuario.password, codigo)  # solo se guarda cifrado
        self.assertFalse(usuario.debe_cambiar_clave)
        self.assertEqual(usuario.creado_por, self.admin)
        self.assertContains(respuesta, "lucia.mora")
        self.assertContains(respuesta, codigo)
        self.assertContains(respuesta, "única vez que se muestra el código de acceso")
        self.assertRedirects(self.client.get(reverse("usuario_credenciales")), reverse("usuarios"))

    def test_nombres_repetidos(self):
        self.client.force_login(self.admin)
        self.crear()
        self.crear()
        self.crear(first_name="Ángel", last_name="Núñez", rol=Rol.ENCARGADO_CIERRE)
        self.assertEqual(
            sorted(Usuario.objects.filter(creado_por=self.admin).values_list("username", flat=True)),
            ["angel.nunez", "lucia.mora", "lucia.mora2"],
        )

    def test_vincula_empleado(self):
        empleado = Empleado.objects.create(identificacion="1710034065", nombres="Lucía", apellidos="Mora",
                                           cargo=Cargo.objects.first(), fecha_ingreso=date.today())
        self.client.force_login(self.admin)
        self.crear(empleado=empleado.pk)
        empleado.refresh_from_db()
        self.assertEqual(empleado.usuario.username, "lucia.mora")

    def test_validaciones(self):
        self.client.force_login(self.admin)
        Usuario.objects.create_user("ya.existe", "ya@x.test", CLAVE)
        form = self.crear(first_name="", email="ya@x.test").context["form"]
        self.assertIn("first_name", form.errors)
        self.assertIn("email", form.errors)

    def test_solo_admin_gestiona_usuarios(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(reverse("usuarios")).status_code, 403)
        self.assertEqual(self.crear().status_code, 403)
        self.assertEqual(self.client.post(reverse("usuario_restablecer_clave", args=[self.admin.pk])).status_code, 403)
        self.assertFalse(Usuario.objects.filter(username="lucia.mora").exists())


class IngresoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("gabo.admin", None, CLAVE, rol=Rol.ADMINISTRADOR)
        cls.lucia = Usuario.objects.create_user("lucia.mora", None, "LM48207153", first_name="Lucía", last_name="Mora")
        cls.lucia2 = Usuario.objects.create_user("lucia.mora2", None, "LM90311274", first_name="Lucía", last_name="Mora")

    def ingresar(self, usuario, codigo):
        return self.client.post(reverse("login"), {"username": usuario, "password": codigo})

    def test_ingreso_con_variantes_del_nombre(self):
        for escrito in ["lucia.mora", "Lucía Mora", "  LUCIA MORA "]:
            with self.subTest(escrito=escrito):
                self.assertRedirects(self.ingresar(escrito, "LM48207153"), reverse("inicio"))
                self.assertEqual(int(self.client.session["_auth_user_id"]), self.lucia.pk)
                self.client.logout()

    def test_iniciales_del_codigo_en_minusculas(self):
        self.assertRedirects(self.ingresar("lucia.mora", "lm48207153"), reverse("inicio"))

    def test_mismo_nombre_se_distingue_por_el_codigo(self):
        self.ingresar("Lucía Mora", "LM90311274")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.lucia2.pk)

    def test_codigo_ajeno_o_incorrecto_no_entra(self):
        casos = [
            ("lucia.mora2", "LM48207153"),  # usuario exacto con el código de otra persona
            ("lucia.mora", "LM00000000"),
            ("nadie.aqui", "LM48207153"),
        ]
        for usuario, codigo in casos:
            with self.subTest(usuario=usuario, codigo=codigo):
                self.client.logout()
                self.assertContains(self.ingresar(usuario, codigo), "Usuario o código de acceso incorrectos")
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_nuevo_codigo_invalida_el_anterior(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("usuario_restablecer_clave", args=[self.lucia.pk]), follow=True)
        self.assertContains(respuesta, "Nuevo código de acceso")
        nuevo = respuesta.context["clave"]
        self.assertRegex(nuevo, r"^LM\d{8}$")
        self.client.logout()
        self.assertContains(self.ingresar("lucia.mora", "LM48207153"), "incorrectos")
        self.assertRedirects(self.ingresar("lucia.mora", nuevo), reverse("inicio"))

    def test_cuenta_desactivada_no_ingresa(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("usuario_cambiar_estado", args=[self.lucia.pk]))
        self.client.logout()
        self.assertContains(self.ingresar("lucia.mora", "LM48207153"), "incorrectos")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_no_se_desactiva_a_si_mismo(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("usuario_cambiar_estado", args=[self.admin.pk]), follow=True)
        self.assertContains(respuesta, "No puede desactivar su propia cuenta")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_dashboard_requiere_sesion(self):
        self.assertRedirects(self.client.get(reverse("inicio")), f"{reverse('login')}?next=/")


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("gabo.admin", None, CLAVE, first_name="Gabo", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vera.ventas", None, CLAVE, rol=Rol.VENDEDOR)

    def test_admin_ve_modulos_indicadores_y_usuarios(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("inicio"))
        for texto in ["Gabo", "Empleados activos", "Usuarios activos", "Materiales con stock bajo", "Personal",
                      "Asistencia", "Inventario", "Nómina", "Cierre de ventas", "Reportes", "Próximamente",
                      reverse("personal:empleado_lista"), reverse("usuarios"), "gabo.admin"]:
            self.assertContains(respuesta, texto)

    def test_vendedor_ve_solo_sus_modulos(self):
        self.client.force_login(self.vendedor)
        respuesta = self.client.get(reverse("inicio"))
        self.assertContains(respuesta, "Ventas")
        self.assertContains(respuesta, "Agenda")
        for texto in ["Empleados activos", "Usuarios activos", "Nómina", "Inventario",
                      reverse("personal:empleado_lista"), reverse("usuarios")]:
            self.assertNotContains(respuesta, texto)

    def test_marcar_notificaciones_leidas(self):
        Notificacion.objects.create(destinatario=self.vendedor, titulo="Hola")
        self.client.force_login(self.vendedor)
        respuesta = self.client.post(reverse("notificaciones_leidas"), HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Hola")
        self.assertFalse(Notificacion.objects.filter(destinatario=self.vendedor, leida=False).exists())


class NotificacionesEnPantallaTests(TestCase):
    """Avisos flotantes (esquina inferior izquierda), campana y avisos de agenda sin cambiar de página."""

    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.lucia = Usuario.objects.create_user("lucia.mora", None, "x", first_name="Lucía", last_name="Mora", rol=Rol.VENDEDOR)
        cls.otro = Usuario.objects.create_user("pedro.paz", None, "x", first_name="Pedro", last_name="Paz", rol=Rol.VENDEDOR)

    def setUp(self):
        self.client.force_login(self.lucia)

    def notificar(self, titulo, usuario=None, **datos):
        return Notificacion.objects.create(destinatario=usuario or self.lucia, titulo=titulo, **datos)

    def consultar(self):
        return self.client.get(reverse("contadores"), HTTP_HX_REQUEST="true")

    def test_la_pagina_tiene_la_zona_de_avisos_y_consulta_al_cargar(self):
        respuesta = self.client.get(reverse("inicio"))
        self.assertContains(respuesta, 'id="avisos-flotantes"')
        self.assertContains(respuesta, "bottom-0 start-0")
        self.assertContains(respuesta, 'hx-trigger="load, every 15s"')

    def test_cada_notificacion_nueva_aparece_una_sola_vez(self):
        self.consultar()  # al entrar: nada nuevo
        self.notificar("Stock bajo de resmas", tipo=Notificacion.Tipo.STOCK_BAJO, url="/inventario/")
        respuesta = self.consultar()
        self.assertContains(respuesta, 'hx-swap-oob="beforeend:#avisos-flotantes"')
        self.assertContains(respuesta, "Stock bajo de resmas")
        self.assertContains(respuesta, 'data-bs-autohide="true"')
        self.assertNotContains(self.consultar(), "Stock bajo de resmas")

    def test_no_se_muestran_las_de_otros_ni_las_viejas_al_iniciar_sesion(self):
        vieja = self.notificar("Aviso antiguo")
        from datetime import timedelta

        from django.utils import timezone

        Notificacion.objects.filter(pk=vieja.pk).update(creado_en=timezone.now() - timedelta(days=1))
        self.notificar("Para Pedro", usuario=self.otro)
        self.notificar("Reciente")
        respuesta = self.consultar()
        self.assertContains(respuesta, "Reciente")
        self.assertNotContains(respuesta, "Aviso antiguo")
        self.assertNotContains(respuesta, "Para Pedro")

    def test_recordatorio_de_agenda_llega_sin_cambiar_de_pagina_y_queda_fijo(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.agenda.models import Evento

        self.consultar()
        Evento.objects.create(propietario=self.lucia, titulo="Llamar a Rosa", tipo=Evento.Tipo.RECORDATORIO,
                              inicio=timezone.now() + timedelta(minutes=10), minutos_aviso=15)
        respuesta = self.consultar()
        self.assertContains(respuesta, "Llamar a Rosa")
        self.assertContains(respuesta, 'data-bs-autohide="false"')
        self.assertContains(respuesta, "Ver evento")

    def test_campana_y_abrir_marca_como_leida(self):
        n = self.notificar("Nueva solicitud", url="/ventas/bases/")
        panel = self.client.get(reverse("notificaciones_panel"), HTTP_HX_REQUEST="true")
        self.assertContains(panel, reverse("notificacion_abrir", args=[n.pk]))
        self.assertContains(panel, "Marcar todas como leídas")
        self.assertRedirects(self.client.get(reverse("notificacion_abrir", args=[n.pk])), "/ventas/bases/", fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertTrue(n.leida)

    def test_no_se_abre_la_de_otro_ni_redirige_fuera(self):
        ajena = self.notificar("Ajena", usuario=self.otro)
        self.assertEqual(self.client.get(reverse("notificacion_abrir", args=[ajena.pk])).status_code, 404)
        externa = self.notificar("Externa", url="https://sitio-externo.com/")
        self.assertRedirects(self.client.get(reverse("notificacion_abrir", args=[externa.pk])),
                             reverse("inicio") + "#notificaciones", fetch_redirect_response=False)
