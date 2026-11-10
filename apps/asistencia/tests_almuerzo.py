"""«Salir a almorzar»: salida, contador en la pantalla de acceso y regreso al iniciar sesión."""
from datetime import date, datetime, time
from unittest import mock

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


@override_settings(ASISTENCIA_ALMUERZO_MINUTOS=60, ASISTENCIA_ALMUERZO_TOLERANCIA_MINUTOS=5)
class AlmuerzoTests(TestCase):
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

    def en(self, momento, metodo, url, datos=None):
        with mock.patch("django.utils.timezone.now", return_value=momento):
            return getattr(self.client, metodo)(url, datos or {}, follow=True)

    def ingresar(self, momento):
        return self.en(momento, "post", reverse("login"), {"username": "luis.andrade", "password": "clave-segura-123"})

    def salir_a_almorzar(self, momento):
        return self.en(momento, "post", reverse("asistencia:salir_almuerzo"))

    def registro(self):
        return RegistroAsistencia.objects.get(empleado=self.empleado)

    def setUp(self):
        self.ingresar(a_las(8, 55))

    # ------------------------------------------------------------------ salida
    def test_salir_a_almorzar_marca_la_hora_y_cierra_la_sesion(self):
        respuesta = self.salir_a_almorzar(a_las(13, 0, 20))
        self.assertEqual(timezone.localtime(self.registro().almuerzo_inicio), a_las(13, 0, 20))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(respuesta, "debe volver a las 14:00")

    def test_el_boton_desaparece_despues_del_almuerzo(self):
        self.assertContains(self.en(a_las(12, 0), "get", reverse("inicio")), reverse("asistencia:salir_almuerzo"))
        self.salir_a_almorzar(a_las(13, 0))
        self.ingresar(a_las(13, 58))
        self.assertNotContains(self.en(a_las(14, 0), "get", reverse("inicio")), reverse("asistencia:salir_almuerzo"))

    def test_solo_un_almuerzo_por_dia(self):
        self.salir_a_almorzar(a_las(13, 0))
        self.ingresar(a_las(13, 55))
        respuesta = self.salir_a_almorzar(a_las(15, 0))
        self.assertContains(respuesta, "Ya salió a almorzar hoy a las 13:00")
        self.assertEqual(timezone.localtime(self.registro().almuerzo_inicio), a_las(13, 0))

    # ------------------------------------------------------------------ contador
    def test_la_pantalla_de_acceso_muestra_el_contador(self):
        self.salir_a_almorzar(a_las(13, 0))
        respuesta = self.en(a_las(13, 30), "get", reverse("login"))
        self.assertContains(respuesta, 'id="contador-almuerzo"')
        self.assertContains(respuesta, "Almuerzo de Luis Andrade")
        self.assertContains(respuesta, "debe volver a las 14:00")

    def test_el_contador_desaparece_al_regresar(self):
        self.salir_a_almorzar(a_las(13, 0))
        self.ingresar(a_las(14, 0))
        self.client.logout()
        self.assertNotContains(self.en(a_las(14, 5), "get", reverse("login")), 'id="contador-almuerzo"')

    def test_una_cookie_alterada_no_muestra_nada(self):
        self.salir_a_almorzar(a_las(13, 0))
        self.client.cookies["callflow_almuerzo"] = "999:firma-falsa"
        self.assertNotContains(self.en(a_las(13, 30), "get", reverse("login")), 'id="contador-almuerzo"')

    # ------------------------------------------------------------------ regreso
    def test_regreso_a_tiempo_no_genera_incidencia(self):
        self.salir_a_almorzar(a_las(13, 0))
        respuesta = self.ingresar(a_las(13, 55))
        self.assertEqual(timezone.localtime(self.registro().almuerzo_fin), a_las(13, 55))
        self.assertContains(respuesta, "Regreso del almuerzo registrado a las 13:55")
        self.assertFalse(NovedadAsistencia.objects.filter(tipo=Tipo.EXCESO_ALMUERZO).exists())

    def test_regreso_dentro_de_la_tolerancia_no_genera_incidencia(self):
        self.salir_a_almorzar(a_las(13, 0))
        self.ingresar(a_las(14, 5))  # 5 min de más = tolerancia
        self.assertFalse(NovedadAsistencia.objects.filter(tipo=Tipo.EXCESO_ALMUERZO).exists())
        self.assertFalse(Notificacion.objects.filter(destinatario=self.admin).exists())

    def test_regreso_tarde_crea_la_incidencia_y_avisa_al_administrador(self):
        self.salir_a_almorzar(a_las(13, 0))
        respuesta = self.ingresar(a_las(14, 12, 10))
        novedad = NovedadAsistencia.objects.get(tipo=Tipo.EXCESO_ALMUERZO)
        self.assertEqual(novedad.minutos, 13)  # todos los minutos de más, cada minuto empezado cuenta
        self.assertEqual(novedad.estado, NovedadAsistencia.Estado.PENDIENTE)
        self.assertContains(respuesta, "Se pasó 13 min de su hora de almuerzo")
        aviso = Notificacion.objects.get(destinatario=self.admin)
        self.assertEqual(aviso.tipo, Notificacion.Tipo.ASISTENCIA)
        self.assertIn("Andrade", aviso.titulo)
        self.assertIn("13 min", aviso.titulo)
        self.assertIn("debía volver a las 14:00", aviso.mensaje)
        self.assertIn("14:12", aviso.mensaje)

    def test_solo_el_primer_ingreso_tras_el_almuerzo_cuenta(self):
        self.salir_a_almorzar(a_las(13, 0))
        self.ingresar(a_las(13, 50))
        self.client.logout()
        self.ingresar(a_las(16, 0))  # cerró y abrió sesión por otro motivo: no es un regreso de almuerzo
        self.assertEqual(timezone.localtime(self.registro().almuerzo_fin), a_las(13, 50))
        self.assertFalse(NovedadAsistencia.objects.filter(tipo=Tipo.EXCESO_ALMUERZO).exists())

    def test_finalizar_el_dia_en_almuerzo_cierra_el_almuerzo(self):
        servicios.iniciar_almuerzo(self.empleado, momento=a_las(13, 0))
        with mock.patch("django.utils.timezone.now", return_value=a_las(18, 0)):
            registro = servicios.finalizar_dia(self.empleado)
        self.assertEqual(timezone.localtime(registro.almuerzo_fin), a_las(18, 0))
        self.assertEqual(timezone.localtime(registro.salida), a_las(18, 0))

    def test_el_tablero_muestra_en_almuerzo(self):
        self.salir_a_almorzar(a_las(13, 0))
        fila = next(f for f in servicios.tablero_del_dia(date(2026, 11, 4)) if f.empleado == self.empleado)
        self.assertEqual(fila.estado, "En almuerzo")
