"""«Finalizar día»: salida al pulsar el botón; incidencia y aviso al administrador si no se pulsa."""
from datetime import date, datetime, time
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol, Usuario
from apps.personal.models import Cargo, Empleado

from . import servicios
from .models import AsignacionHorario, Horario, NovedadAsistencia, RegistroAsistencia

ZONA = timezone.get_default_timezone()
Tipo = NovedadAsistencia.Tipo


def a_las(h, m, s=0, dia=date(2026, 11, 4)):  # miércoles
    return datetime.combine(dia, time(h, m, s), tzinfo=ZONA)


@override_settings(ASISTENCIA_MARGEN_SALIDA_MINUTOS=60)
class FinalizarDiaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.vendedor = Usuario.objects.create_user("luis.andrade", None, "clave-segura-123", rol=Rol.VENDEDOR)
        cls.empleado = Empleado.objects.create(
            identificacion="1710034065", nombres="Luis", apellidos="Andrade", usuario=cls.vendedor,
            cargo=Cargo.objects.get(nombre="Vendedor telefónico"), fecha_ingreso=date(2026, 1, 1),
        )
        horario = Horario.objects.create(nombre="Jornada 9 a 18", hora_entrada=time(9, 0), hora_salida=time(18, 0), minutos_tolerancia=0)
        AsignacionHorario.objects.create(empleado=cls.empleado, horario=horario, vigente_desde=date(2026, 1, 1))
        cls.admin = Usuario.objects.create_user("admin1", None, "clave-segura-123", rol=Rol.ADMINISTRADOR)
        cls.admin_inactivo = Usuario.objects.create_user("admin2", None, "clave-segura-123", rol=Rol.ADMINISTRADOR, is_active=False)

    def setUp(self):
        cache.clear()  # la revisión desde la campana se limita a una cada 5 minutos

    def ingresar(self, momento):
        with mock.patch("django.utils.timezone.now", return_value=momento):
            self.client.post(reverse("login"), {"username": "luis.andrade", "password": "clave-segura-123"})

    def finalizar(self, momento):
        with mock.patch("django.utils.timezone.now", return_value=momento):
            return self.client.post(reverse("asistencia:finalizar_dia"), follow=True)

    def revisar(self, momento):
        return servicios.revisar_salidas_pendientes(ahora=momento)

    # ------------------------------------------------------------------ botón
    def test_el_boton_aparece_mientras_la_jornada_esta_abierta(self):
        self.ingresar(a_las(8, 55))
        with mock.patch("django.utils.timezone.now", return_value=a_las(10, 0)):
            respuesta = self.client.get(reverse("inicio"))
        self.assertContains(respuesta, reverse("asistencia:finalizar_dia"))

    def test_el_administrador_no_ve_el_boton(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("inicio"))
        self.assertNotContains(respuesta, reverse("asistencia:finalizar_dia"))

    def test_finalizar_registra_la_salida_y_cierra_la_sesion(self):
        self.ingresar(a_las(8, 55))
        respuesta = self.finalizar(a_las(18, 2, 30))
        registro = RegistroAsistencia.objects.get(empleado=self.empleado)
        self.assertEqual(timezone.localtime(registro.salida), a_las(18, 2, 30))
        self.assertContains(respuesta, "Salida registrada a las 18:02:30")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(NovedadAsistencia.objects.filter(tipo=Tipo.SALIDA_ANTICIPADA).exists())

    def test_finalizar_antes_de_hora_queda_como_salida_anticipada(self):
        self.ingresar(a_las(8, 55))
        respuesta = self.finalizar(a_las(17, 0))
        self.assertEqual(NovedadAsistencia.objects.get(tipo=Tipo.SALIDA_ANTICIPADA).minutos, 60)
        self.assertContains(respuesta, "salida anticipada")

    def test_no_se_puede_finalizar_dos_veces(self):
        self.ingresar(a_las(8, 55))
        self.finalizar(a_las(18, 5))
        self.ingresar(a_las(18, 10))  # vuelve a entrar el mismo día: no hay jornada abierta
        respuesta = self.finalizar(a_las(18, 15))
        self.assertContains(respuesta, "ya tiene la salida registrada")
        registro = RegistroAsistencia.objects.get(empleado=self.empleado)
        self.assertEqual(timezone.localtime(registro.salida), a_las(18, 5))

    def test_finalizar_exige_post(self):
        self.ingresar(a_las(8, 55))
        self.assertEqual(self.client.get(reverse("asistencia:finalizar_dia")).status_code, 405)

    # ------------------------------------------------------------------ sin finalizar
    def test_antes_del_margen_no_hay_incidencia(self):
        self.ingresar(a_las(8, 55))
        self.assertEqual(self.revisar(a_las(18, 59)), 0)  # salida 18:00 + 60 min de margen
        self.assertFalse(NovedadAsistencia.objects.filter(tipo=Tipo.SIN_SALIDA).exists())

    def test_sin_finalizar_se_crea_la_incidencia_y_se_avisa_a_los_administradores(self):
        self.ingresar(a_las(8, 55))
        self.assertEqual(self.revisar(a_las(19, 1)), 1)
        novedad = NovedadAsistencia.objects.get(tipo=Tipo.SIN_SALIDA)
        self.assertEqual((novedad.empleado, novedad.fecha, novedad.estado), (self.empleado, date(2026, 11, 4), NovedadAsistencia.Estado.PENDIENTE))
        self.assertIsNone(novedad.minutos)
        aviso = Notificacion.objects.get(destinatario=self.admin)
        self.assertEqual(aviso.tipo, Notificacion.Tipo.ASISTENCIA)
        self.assertIn("Andrade", aviso.titulo)
        self.assertIn("04/11/2026", aviso.mensaje)
        self.assertFalse(Notificacion.objects.filter(destinatario=self.admin_inactivo).exists())
        self.assertFalse(Notificacion.objects.filter(destinatario=self.vendedor).exists())

    def test_la_revision_es_idempotente(self):
        self.ingresar(a_las(8, 55))
        self.revisar(a_las(19, 1))
        self.assertEqual(self.revisar(a_las(19, 30)), 0)
        self.assertEqual(self.revisar(a_las(9, 0, dia=date(2026, 11, 5))), 0)
        self.assertEqual(NovedadAsistencia.objects.filter(tipo=Tipo.SIN_SALIDA).count(), 1)
        self.assertEqual(Notificacion.objects.filter(destinatario=self.admin).count(), 1)

    def test_quien_finalizo_no_genera_incidencia(self):
        self.ingresar(a_las(8, 55))
        self.finalizar(a_las(18, 0))
        self.assertEqual(self.revisar(a_las(23, 0)), 0)

    def test_registros_antiguos_no_generan_alertas(self):
        RegistroAsistencia.objects.create(empleado=self.empleado, fecha=date(2026, 10, 1), entrada=a_las(9, 0, dia=date(2026, 10, 1)))
        self.assertEqual(self.revisar(a_las(10, 0)), 0)

    def test_corregir_la_salida_quita_la_incidencia_pendiente(self):
        self.ingresar(a_las(8, 55))
        self.revisar(a_las(19, 1))
        servicios.registrar_manual(self.empleado, date(2026, 11, 4), time(8, 55), time(18, 0), usuario=self.admin)
        self.assertFalse(NovedadAsistencia.objects.filter(tipo=Tipo.SIN_SALIDA).exists())

    def test_la_consulta_de_la_campana_tambien_revisa(self):
        self.ingresar(a_las(8, 55))
        self.client.force_login(self.admin)
        with mock.patch("django.utils.timezone.now", return_value=a_las(19, 5)):
            self.client.get(reverse("contadores"))
        self.assertTrue(NovedadAsistencia.objects.filter(tipo=Tipo.SIN_SALIDA).exists())

    def test_comando_revisar_salidas(self):
        self.ingresar(a_las(8, 55))
        with mock.patch("django.utils.timezone.now", return_value=a_las(19, 5)):
            call_command("revisar_salidas", verbosity=0)
        self.assertTrue(NovedadAsistencia.objects.filter(tipo=Tipo.SIN_SALIDA).exists())
