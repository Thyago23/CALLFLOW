"""Breaks: el coordinador los da, les llega a los vendedores y se registra quién volvió tarde."""
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol, Usuario
from apps.personal.models import Cargo, Empleado

from . import breaks
from .models import Descanso, RegistroAsistencia, RegresoDescanso

ZONA = timezone.get_default_timezone()
DIA = date(2026, 11, 4)


def a_las(h, m, s=0):
    return datetime.combine(DIA, time(h, m, s), tzinfo=ZONA)


@override_settings(ASISTENCIA_BREAK_TOLERANCIA_MINUTOS=0)
class BreaksTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cargo_venta = Cargo.objects.get(nombre="Vendedor telefónico")
        cls.coordinador = Usuario.objects.create_user("marlon.rosero", None, "clave-segura-123", rol=Rol.ENCARGADO_CIERRE,
                                                      first_name="Marlon", last_name="Rosero de Mendoza")
        cls.coordinador.user_permissions.add(Permission.objects.get(content_type__app_label="asistencia", codename="gestionar_breaks"))
        cls.admin = Usuario.objects.create_superuser("admin.callflow", None, "clave-segura-123")
        cls.vendedores = []
        for i, (nombre, cedula) in enumerate((("Luis", "1710034065"), ("Ana", "1103000004"), ("Pedro", "0926687856"))):
            u = Usuario.objects.create_user(f"{nombre.lower()}.prueba", None, "clave-segura-123", rol=Rol.VENDEDOR)
            e = Empleado.objects.create(identificacion=cedula, nombres=nombre, apellidos="Prueba", usuario=u, cargo=cargo_venta,
                                        fecha_ingreso=date(2026, 1, 1))
            cls.vendedores.append(e)
        cls.luis, cls.ana, cls.pedro = cls.vendedores

    def setUp(self):
        # Luis y Ana trabajando; Pedro está en su almuerzo (no recibe el break).
        RegistroAsistencia.objects.create(empleado=self.luis, fecha=DIA, entrada=a_las(9, 0))
        RegistroAsistencia.objects.create(empleado=self.ana, fecha=DIA, entrada=a_las(9, 0))
        RegistroAsistencia.objects.create(empleado=self.pedro, fecha=DIA, entrada=a_las(9, 0), almuerzo_inicio=a_las(10, 0))

    def en(self, momento, usuario, metodo, url, datos=None, **extra):
        self.client.force_login(usuario)
        with mock.patch("django.utils.timezone.now", return_value=momento):
            return getattr(self.client, metodo)(url, datos or {}, follow=True, **extra)

    def dar(self, momento=None, minutos=15, mensaje=""):
        return self.en(momento or a_las(10, 30), self.coordinador, "post", reverse("asistencia:breaks"), {"minutos": minutos, "mensaje": mensaje})

    def volver(self, empleado, momento):
        return self.en(momento, empleado.usuario, "post", reverse("asistencia:volvi_break"))

    # ------------------------------------------------------------------ acceso
    def test_solo_el_coordinador_da_breaks(self):
        self.assertContains(self.en(a_las(10, 0), self.coordinador, "get", reverse("inicio")), reverse("asistencia:breaks"))
        for usuario in (self.admin, self.luis.usuario):
            self.assertNotContains(self.en(a_las(10, 0), usuario, "get", reverse("inicio")), reverse("asistencia:breaks"))
            self.assertEqual(self.en(a_las(10, 0), usuario, "get", reverse("asistencia:breaks")).status_code, 403)
            self.assertEqual(self.en(a_las(10, 0), usuario, "post", reverse("asistencia:breaks"), {"minutos": 15}).status_code, 403)
        self.assertFalse(Descanso.objects.exists())

    # ------------------------------------------------------------------ dar el break
    def test_dar_break_avisa_a_los_vendedores_con_jornada_abierta(self):
        respuesta = self.dar(minutos=15, mensaje="¡Buen trabajo!")
        self.assertContains(respuesta, "enviado a 2 vendedores")
        d = Descanso.objects.get()
        self.assertEqual((timezone.localtime(d.inicio), timezone.localtime(d.fin)), (a_las(10, 30), a_las(10, 45)))
        self.assertEqual({r.empleado for r in d.regresos.all()}, {self.luis, self.ana})  # Pedro está almorzando
        avisos = Notificacion.objects.filter(titulo="¡Break de 15 minutos!")
        self.assertEqual({n.destinatario for n in avisos}, {self.luis.usuario, self.ana.usuario})
        self.assertIn("vuelve a las 10:45", avisos.first().mensaje)
        self.assertIn("¡Buen trabajo!", avisos.first().mensaje)

    def test_no_se_da_otro_break_mientras_hay_uno_en_curso(self):
        self.dar(a_las(10, 30), 15)
        self.assertContains(self.dar(a_las(10, 40), 10), "Ya hay un break en curso hasta las 10:45")
        self.dar(a_las(10, 46), 10)
        self.assertEqual(Descanso.objects.count(), 2)

    def test_duracion_valida_y_sin_vendedores(self):
        self.assertContains(self.dar(minutos=0), "entre 1 y 120")
        self.assertContains(self.dar(minutos=500), "entre 1 y 120")
        RegistroAsistencia.objects.update(salida=a_las(10, 0))
        self.assertContains(self.dar(), "No hay vendedores con la jornada abierta")
        self.assertFalse(Descanso.objects.exists())

    # ------------------------------------------------------------------ lado del vendedor
    def test_el_vendedor_ve_el_aviso_con_la_cuenta_atras(self):
        self.dar(a_las(10, 30), 15)
        pagina = self.en(a_las(10, 35), self.luis.usuario, "get", reverse("inicio"))
        self.assertContains(pagina, "Volví del break")
        self.assertContains(pagina, "vuelve a las 10:45")
        # También llega con el sondeo de la campana (sin recargar la página).
        sondeo = self.en(a_las(10, 35), self.luis.usuario, "get", reverse("contadores"))
        self.assertContains(sondeo, 'id="aviso-break" hx-swap-oob="innerHTML"')
        self.assertContains(sondeo, "Volví del break")
        # Pedro (en almuerzo) no lo ve.
        self.assertNotContains(self.en(a_las(10, 35), self.pedro.usuario, "get", reverse("inicio")), reverse("asistencia:volvi_break"))

    def test_volver_a_tiempo(self):
        self.dar(a_las(10, 30), 15)
        respuesta = self.volver(self.luis, a_las(10, 44, 59))
        self.assertContains(respuesta, "a tiempo")
        self.assertNotContains(respuesta, 'class="alert alert-info aviso-break')  # el aviso desaparece
        r = RegresoDescanso.objects.get(empleado=self.luis)
        self.assertEqual((timezone.localtime(r.regreso_en), r.minutos_tarde, r.tarde), (a_las(10, 44, 59), 0, False))
        self.assertFalse(Notificacion.objects.filter(destinatario=self.coordinador).exists())

    def test_volver_tarde_avisa_al_coordinador(self):
        self.dar(a_las(10, 30), 15)
        respuesta = self.volver(self.ana, a_las(10, 48, 10))  # 3 min 10 s tarde → 4 min (cada minuto empezado cuenta)
        self.assertContains(respuesta, "volviste 4 min tarde")
        r = RegresoDescanso.objects.get(empleado=self.ana)
        self.assertEqual(r.minutos_tarde, 4)
        aviso = Notificacion.objects.get(destinatario=self.coordinador)
        self.assertEqual(aviso.titulo, "Prueba Ana volvió 4 min tarde del break")
        self.assertIn("terminaba a las 10:45 y marcó su regreso a las 10:48", aviso.mensaje)

    @override_settings(ASISTENCIA_BREAK_TOLERANCIA_MINUTOS=2)
    def test_tolerancia(self):
        self.dar(a_las(10, 30), 15)
        self.volver(self.luis, a_las(10, 46, 30))  # 2 min: dentro de la tolerancia
        self.volver(self.ana, a_las(10, 47, 30))  # 3 min: tarde
        self.assertEqual(RegresoDescanso.objects.get(empleado=self.luis).minutos_tarde, 0)
        self.assertEqual(RegresoDescanso.objects.get(empleado=self.ana).minutos_tarde, 3)

    def test_volver_sin_break_o_dos_veces(self):
        self.assertContains(self.volver(self.luis, a_las(10, 0)), "No tiene un break pendiente")
        self.dar(a_las(10, 30), 15)
        self.volver(self.luis, a_las(10, 40))
        self.assertContains(self.volver(self.luis, a_las(10, 41)), "No tiene un break pendiente")
        self.assertEqual(timezone.localtime(RegresoDescanso.objects.get(empleado=self.luis).regreso_en), a_las(10, 40))

    # ------------------------------------------------------------------ pantalla del coordinador
    def test_el_coordinador_ve_quien_llego_tarde_y_quien_no_marco(self):
        self.dar(a_las(10, 30), 15)
        self.volver(self.ana, a_las(10, 50))
        panel = self.en(a_las(10, 52), self.coordinador, "get", reverse("asistencia:breaks_panel"))
        self.assertContains(panel, "Volvió 5 min tarde (10:50)")
        self.assertContains(panel, "No ha marcado su regreso (+7 min)")  # Luis
        detalle = breaks.detalle(Descanso.objects.get(), a_las(10, 52))
        self.assertEqual((detalle["cuenta"]["tarde"], detalle["cuenta"]["sin_volver"]), (1, 1))
        self.assertEqual(detalle["filas"][0].regreso.empleado, self.luis)  # primero quien no vuelve

    def test_servicio_valida_el_permiso(self):
        with self.assertRaises(ValidationError):
            breaks.dar_break(self.admin, 15, ahora=a_las(10, 0))
        self.assertEqual(breaks.minutos_tarde(a_las(10, 45), a_las(10, 45)), 0)
        self.assertEqual(breaks.minutos_tarde(a_las(10, 45), a_las(10, 45) + timedelta(seconds=1)), 1)
