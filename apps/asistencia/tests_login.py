"""Entrada automática al iniciar sesión (hora exacta del primer ingreso del día)."""
from datetime import date, datetime, time
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Rol, Usuario
from apps.personal.models import Cargo, Empleado

from .models import AsignacionHorario, Horario, NovedadAsistencia, RegistroAsistencia

ZONA = timezone.get_default_timezone()


def a_las(h, m, s=0, dia=date(2026, 11, 4)):  # miércoles
    return datetime.combine(dia, time(h, m, s), tzinfo=ZONA)


class EntradaAlIniciarSesionTests(TestCase):
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

    def ingresar(self, usuario, momento):
        with mock.patch("django.utils.timezone.now", return_value=momento):
            return self.client.post(reverse("login"), {"username": usuario.username, "password": "clave-segura-123"}, follow=True)

    def test_primer_ingreso_registra_la_hora_exacta_con_segundos(self):
        respuesta = self.ingresar(self.vendedor, a_las(8, 57, 42))
        registro = RegistroAsistencia.objects.get(empleado=self.empleado)
        self.assertEqual(timezone.localtime(registro.entrada), a_las(8, 57, 42))
        self.assertEqual(registro.origen, RegistroAsistencia.Origen.SISTEMA)
        self.assertEqual(registro.registrado_por, self.vendedor)
        self.assertContains(respuesta, "Entrada registrada a las 08:57:42")

    def test_los_siguientes_ingresos_del_dia_no_cambian_la_entrada(self):
        self.ingresar(self.vendedor, a_las(8, 57))
        self.client.logout()
        self.ingresar(self.vendedor, a_las(13, 5))
        registro = RegistroAsistencia.objects.get(empleado=self.empleado)
        self.assertEqual(timezone.localtime(registro.entrada), a_las(8, 57))

    def test_ingreso_tarde_genera_el_atraso(self):
        self.ingresar(self.vendedor, a_las(9, 12, 30))
        atraso = NovedadAsistencia.objects.get(empleado=self.empleado, tipo=NovedadAsistencia.Tipo.ATRASO)
        self.assertEqual(atraso.minutos, 13)  # cada minuto empezado cuenta (9:12:30 → 13)

    def test_otro_dia_es_otra_entrada(self):
        self.ingresar(self.vendedor, a_las(8, 55))
        self.client.logout()
        self.ingresar(self.vendedor, a_las(8, 58, dia=date(2026, 11, 5)))
        self.assertEqual(RegistroAsistencia.objects.filter(empleado=self.empleado).count(), 2)

    def test_administrador_no_marca_entrada(self):
        self.ingresar(self.admin, a_las(9, 30))
        self.assertFalse(RegistroAsistencia.objects.exists())

    def test_empleado_de_vacaciones_ingresa_pero_no_marca(self):
        Empleado.objects.filter(pk=self.empleado.pk).update(estado=Empleado.Estado.VACACIONES)
        respuesta = self.ingresar(self.vendedor, a_las(9, 0))
        self.assertTrue(respuesta.context["user"].is_authenticated)
        self.assertFalse(RegistroAsistencia.objects.exists())
        self.assertContains(respuesta, "No se registró su entrada")
