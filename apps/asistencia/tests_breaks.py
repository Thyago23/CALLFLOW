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
class BaseBreaks(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cargo_venta = Cargo.objects.get(nombre="Asesor de ventas")
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


class BreaksTests(BaseBreaks):
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


class BreaksProgramadosTests(BaseBreaks):
    """Break a una hora específica: recordatorio 10 o 5 min antes y empieza solo (los breaks de siempre siguen igual)."""

    def programar(self, h, m, minutos=15, aviso=10, ahora=None, mensaje=""):
        return breaks.programar_break(self.coordinador, a_las(h, m), minutos, aviso, mensaje, ahora=ahora or a_las(9, 30))

    def avisos(self, empleado):
        return list(Notificacion.objects.filter(destinatario=empleado.usuario).order_by("pk").values_list("titulo", flat=True))

    def test_recordatorio_y_arranque_a_la_hora(self):
        p = self.programar(10, 30, 15, aviso=10, mensaje="Media mañana")
        self.assertEqual(breaks.procesar_programados(a_las(10, 19)), 0)  # todavía no toca
        self.assertEqual(self.avisos(self.luis), [])
        self.assertEqual(breaks.procesar_programados(a_las(10, 20)), 1)
        p.refresh_from_db()
        self.assertEqual((p.estado, p.avisados), ("AVISADO", 2))  # Luis y Ana (Pedro está almorzando)
        self.assertEqual(self.avisos(self.luis), ["Tu break empieza en 10 minutos"])
        self.assertEqual(self.avisos(self.pedro), [])
        self.assertEqual(breaks.procesar_programados(a_las(10, 25)), 0)  # el recordatorio sale una sola vez
        self.assertEqual(breaks.proximo_para(self.luis, a_las(10, 25)), p)

        breaks.procesar_programados(a_las(10, 30, 4))
        p.refresh_from_db()
        d = p.descanso
        self.assertEqual((p.estado, d.duracion_minutos, d.mensaje), ("INICIADO", 15, "Media mañana"))
        # El tiempo corre desde la hora programada, no desde que se procesó.
        self.assertEqual((timezone.localtime(d.inicio), timezone.localtime(d.fin)), (a_las(10, 30), a_las(10, 45)))
        self.assertEqual(self.avisos(self.luis)[-1], "¡Break de 15 minutos!")
        # Desde aquí es el mismo flujo de siempre: marca su regreso.
        self.volver(self.luis, a_las(10, 44))
        self.assertEqual(RegresoDescanso.objects.get(empleado=self.luis).minutos_tarde, 0)

    def test_aviso_de_5_minutos(self):
        self.programar(11, 0, 10, aviso=5)
        breaks.procesar_programados(a_las(10, 54))
        self.assertEqual(self.avisos(self.ana), [])
        breaks.procesar_programados(a_las(10, 55))
        self.assertEqual(self.avisos(self.ana), ["Tu break empieza en 5 minutos"])

    def test_el_vendedor_ve_la_cuenta_atras_antes_de_que_empiece(self):
        self.programar(10, 30)
        pagina = self.en(a_las(10, 22), self.luis.usuario, "get", reverse("contadores"))  # la campana procesa y avisa
        self.assertContains(pagina, "Tu break de 15 min empieza a las 10:30")
        self.assertContains(pagina, "data-texto-vencido")
        self.assertNotContains(pagina, "Volví del break")
        empezado = self.en(a_las(10, 30, 2), self.luis.usuario, "get", reverse("contadores"))
        self.assertContains(empezado, "Volví del break")

    def test_validaciones_al_programar(self):
        with self.assertRaises(ValidationError):
            self.programar(9, 0)  # ya pasó
        with self.assertRaises(ValidationError):
            self.programar(10, 30, aviso=7)
        with self.assertRaises(ValidationError):
            self.programar(10, 30, minutos=0)
        with self.assertRaises(ValidationError):
            breaks.programar_break(self.admin, a_las(10, 30), 15, ahora=a_las(9, 30))  # sin el permiso de coordinador
        self.programar(10, 30, 15)
        with self.assertRaises(ValidationError):
            self.programar(10, 40, 10)  # se cruza con el de 10:30 a 10:45
        self.programar(10, 45, 10)  # justo al terminar: se puede

    def test_cancelar_avisa_si_ya_habia_recordatorio(self):
        p = self.programar(10, 30)
        breaks.procesar_programados(a_las(10, 21))
        breaks.cancelar_programado(self.coordinador, p.pk, ahora=a_las(10, 22))
        p.refresh_from_db()
        self.assertEqual(p.estado, "CANCELADO")
        self.assertEqual(self.avisos(self.luis)[-1], "Se canceló el break de las 10:30")
        breaks.procesar_programados(a_las(10, 31))
        self.assertFalse(Descanso.objects.exists())
        with self.assertRaises(ValidationError):
            breaks.cancelar_programado(self.coordinador, p.pk)

    def test_se_omite_si_hay_un_break_en_curso_y_avisa_al_coordinador(self):
        p = self.programar(10, 30)
        self.dar(a_las(10, 25), 15)  # el coordinador dio uno a mano que sigue a las 10:30
        breaks.procesar_programados(a_las(10, 30))
        p.refresh_from_db()
        self.assertEqual(p.estado, "OMITIDO")
        self.assertEqual(Descanso.objects.count(), 1)
        self.assertTrue(Notificacion.objects.filter(destinatario=self.coordinador, titulo__contains="no empezó").exists())

    def test_se_omite_si_paso_su_horario_sin_procesar(self):
        p = self.programar(10, 30, 15)
        breaks.procesar_programados(a_las(11, 0))
        p.refresh_from_db()
        self.assertEqual(p.estado, "OMITIDO")
        self.assertFalse(Descanso.objects.exists())

    def test_pantalla_del_coordinador(self):
        r = self.en(a_las(9, 30), self.coordinador, "post", reverse("asistencia:programar_break"),
                    {"fecha": "2026-11-04", "hora": "10:30", "minutos": "20", "aviso": "5", "mensaje": ""})
        self.assertContains(r, "Break de 20 min programado para hoy a las 10:30")
        self.assertContains(r, "Se recordará 5 min antes (10:25)")
        self.assertContains(r, "Programar un break")
        p = breaks.programados(a_las(9, 30))["pendientes"][0]
        r = self.en(a_las(9, 31), self.coordinador, "post", reverse("asistencia:cancelar_break_programado", args=[p.pk]))
        self.assertContains(r, "Se canceló el break programado de las 10:30")
        self.client.force_login(self.luis.usuario)
        self.assertEqual(self.client.post(reverse("asistencia:programar_break"), {}).status_code, 403)
