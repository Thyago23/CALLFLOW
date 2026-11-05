"""Agenda: SCRUM-28 registrar actividades, reuniones y recordatorios; SCRUM-29 avisos de pendientes."""
from datetime import date, datetime, time, timedelta

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol, Usuario

from . import servicios
from .models import Evento, ParticipanteEvento


def proximo_laborable(desde: date | None = None, saltar: int = 1) -> date:
    """El siguiente día laborable (lunes a viernes) después de `desde`, para que las pruebas no fallen en fin de semana."""
    dia = desde or timezone.localdate()
    while saltar:
        dia += timedelta(days=1)
        if dia.isoweekday() <= 5:
            saltar -= 1
    return dia


def a_las(dia: date, hh: int, mm: int = 0):
    return timezone.make_aware(datetime.combine(dia, time(hh, mm)), timezone.get_current_timezone())


class BaseAgenda(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.diego = Usuario.objects.create_user("diego.mendoza", None, "x", first_name="Diego", last_name="Mendoza", rol=Rol.ADMINISTRADOR)
        cls.lucia = Usuario.objects.create_user("lucia.mora", None, "x", first_name="Lucía", last_name="Mora", rol=Rol.VENDEDOR)
        cls.ana = Usuario.objects.create_user("ana.torres", None, "x", first_name="Ana", last_name="Torres", rol=Rol.ENCARGADO_CIERRE)

    def evento(self, dueño=None, inicio=None, invitados=(), **datos):
        evento = Evento(propietario=dueño or self.diego, titulo=datos.pop("titulo", "Reunión de ventas"),
                        inicio=inicio or timezone.now() + timedelta(hours=2), **datos)
        return servicios.guardar_evento(evento, list(invitados), avisar_de_nuevo=True)


class RegistrarEventosTests(BaseAgenda):
    """SCRUM-28"""

    def test_crear_desde_la_pantalla(self):
        self.client.force_login(self.lucia)
        manana = proximo_laborable()
        respuesta = self.client.post(reverse("agenda:crear"), {
            "titulo": "  Llamar a cliente   VIP ", "tipo": "RECORDATORIO", "fecha": manana.isoformat(),
            "hora_inicio": "10:30", "hora_fin": "", "ubicacion": "", "minutos_aviso": "15",
            "participantes": [self.ana.pk], "descripcion": "",
        }, follow=True)
        evento = Evento.objects.get()
        self.assertEqual((evento.titulo, evento.tipo, evento.propietario), ("Llamar a cliente VIP", "RECORDATORIO", self.lucia))
        self.assertEqual(timezone.localtime(evento.inicio), a_las(manana, 10, 30))
        self.assertEqual(list(evento.participantes.values_list("usuario", flat=True)), [self.ana.pk])
        self.assertContains(respuesta, "agregado a la agenda")

    def test_todo_el_dia_y_validaciones(self):
        self.client.force_login(self.diego)
        hoy = proximo_laborable().isoformat()
        self.client.post(reverse("agenda:crear"), {"titulo": "Inventario anual", "tipo": "ACTIVIDAD", "fecha": hoy,
                                                   "todo_el_dia": "on", "minutos_aviso": "0"})
        evento = Evento.objects.get()
        self.assertTrue(evento.todo_el_dia)
        self.assertIsNone(evento.fin)
        form = self.client.post(reverse("agenda:crear"), {"titulo": "X", "tipo": "REUNION", "fecha": hoy,
                                                          "hora_inicio": "15:00", "hora_fin": "14:00", "minutos_aviso": "15"}).context["form"]
        self.assertIn("hora_fin", form.errors)
        form = self.client.post(reverse("agenda:crear"), {"titulo": "X", "tipo": "REUNION", "fecha": hoy, "minutos_aviso": "15"}).context["form"]
        self.assertIn("hora_inicio", form.errors)

    def test_cada_quien_ve_lo_suyo_y_lo_que_le_invitaron(self):
        propio = self.evento(titulo="Plan semanal")
        invitado = self.evento(dueño=self.ana, titulo="Revisión de cierres", invitados=[self.diego])
        ajeno = self.evento(dueño=self.lucia, titulo="Algo privado")
        self.assertEqual(set(servicios.eventos_de(self.diego)), {propio, invitado})
        self.client.force_login(self.diego)
        self.assertEqual(self.client.get(reverse("agenda:detalle", args=[ajeno.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("agenda:detalle", args=[invitado.pk])).status_code, 200)
        # Solo el dueño edita o elimina.
        self.assertEqual(self.client.get(reverse("agenda:editar", args=[invitado.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("agenda:eliminar", args=[invitado.pk])).status_code, 403)

    def test_completar_cancelar_editar_y_eliminar(self):
        evento = self.evento(inicio=a_las(proximo_laborable(), 10))
        self.client.force_login(self.diego)
        self.client.post(reverse("agenda:estado", args=[evento.pk]), {"estado": "COMPLETADO"})
        evento.refresh_from_db()
        self.assertEqual(evento.estado, "COMPLETADO")
        self.client.post(reverse("agenda:estado", args=[evento.pk]), {"estado": "PENDIENTE", "volver": "https://sitio-externo.com/"})
        evento.refresh_from_db()
        self.assertEqual(evento.estado, "PENDIENTE")
        dia = timezone.localtime(evento.inicio).date()
        self.client.post(reverse("agenda:editar", args=[evento.pk]), {
            "titulo": "Reunión de ventas (cambio de sala)", "tipo": "REUNION", "fecha": dia.isoformat(),
            "hora_inicio": "16:00", "hora_fin": "17:00", "ubicacion": "Sala 2", "minutos_aviso": "30"})
        evento.refresh_from_db()
        self.assertEqual((evento.titulo, evento.ubicacion, evento.minutos_aviso), ("Reunión de ventas (cambio de sala)", "Sala 2", 30))
        self.client.post(reverse("agenda:eliminar", args=[evento.pk]))
        self.assertFalse(Evento.objects.exists())

    def test_volver_externo_no_redirige_fuera(self):
        evento = self.evento()
        self.client.force_login(self.diego)
        respuesta = self.client.post(reverse("agenda:estado", args=[evento.pk]), {"estado": "COMPLETADO", "volver": "https://sitio-externo.com/"})
        self.assertRedirects(respuesta, reverse("agenda:detalle", args=[evento.pk]))

    def test_vistas_mes_semana_y_dia(self):
        dia = date(2026, 11, 18)
        self.evento(inicio=a_las(dia, 9), titulo="Capacitación DAMI")
        self.evento(inicio=a_las(dia, 14), titulo="Reunión con encargados", tipo="REUNION")
        self.client.force_login(self.diego)
        for vista in ["mes", "semana", "dia"]:
            with self.subTest(vista=vista):
                respuesta = self.client.get(reverse("agenda:calendario"), {"vista": vista, "fecha": dia.isoformat()})
                self.assertContains(respuesta, "Capacitación DAMI")
                self.assertContains(respuesta, "Reunión con encargados")
        semanas = servicios.semanas_del_mes(self.diego, dia)
        celda = next(d for s in semanas for d in s if d.fecha == dia)
        self.assertEqual([e.titulo for e in celda.eventos], ["Capacitación DAMI", "Reunión con encargados"])
        self.assertEqual(semanas[0][0].fecha.weekday(), 0)  # las semanas empiezan en lunes


class AvisosTests(BaseAgenda):
    """SCRUM-29"""

    def test_aviso_llega_antes_al_dueño_y_a_los_invitados_una_sola_vez(self):
        inicio = timezone.now() + timedelta(minutes=40)
        evento = self.evento(inicio=inicio, minutos_aviso=30, invitados=[self.lucia])
        self.assertEqual(servicios.generar_avisos(ahora=inicio - timedelta(minutes=31)), 0)  # todavía no
        self.assertEqual(servicios.generar_avisos(ahora=inicio - timedelta(minutes=30)), 1)
        avisos = Notificacion.objects.filter(tipo="AGENDA")
        self.assertEqual(set(avisos.values_list("destinatario__username", flat=True)), {"diego.mendoza", "lucia.mora"})
        self.assertEqual(avisos.first().url, reverse("agenda:detalle", args=[evento.pk]))
        self.assertIn("Hoy a las", avisos.first().mensaje)
        self.assertEqual(servicios.generar_avisos(ahora=inicio), 0)  # no se repite
        self.assertEqual(avisos.count(), 2)

    def test_eventos_completados_o_pasados_no_avisan(self):
        self.evento(inicio=timezone.now() - timedelta(hours=3), titulo="Ya pasó")
        completado = self.evento(inicio=timezone.now() + timedelta(minutes=5))
        servicios.cambiar_estado(completado, Evento.Estado.COMPLETADO)
        self.assertEqual(servicios.generar_avisos(ahora=timezone.now() + timedelta(minutes=10)), 0)

    def test_evento_cercano_avisa_de_inmediato(self):
        """Evento en 10 minutos con aviso de 15: el momento del aviso ya pasó, pero el evento no."""
        self.evento(inicio=timezone.now() + timedelta(minutes=10), minutos_aviso=15)
        self.assertEqual(servicios.generar_avisos(), 1)

    def test_todo_el_dia_avisa_antes_del_inicio_de_la_jornada(self):
        manana = proximo_laborable()
        self.evento(inicio=a_las(manana, 0), todo_el_dia=True, minutos_aviso=15, titulo="Auditoría")
        self.assertEqual(servicios.generar_avisos(ahora=a_las(manana, 8, 44)), 0)
        self.assertEqual(servicios.generar_avisos(ahora=a_las(manana, 8, 45)), 1)  # 15 min antes de las 09:00

    def test_cambiar_la_hora_reprograma_el_aviso(self):
        inicio = timezone.now() + timedelta(minutes=10)
        evento = self.evento(inicio=inicio, minutos_aviso=15)
        servicios.generar_avisos(ahora=timezone.now())
        evento.refresh_from_db()
        self.assertTrue(evento.aviso_enviado)
        evento.inicio = timezone.now() + timedelta(days=2)
        servicios.guardar_evento(evento, [], avisar_de_nuevo=True)
        self.assertFalse(evento.aviso_enviado)

    def test_los_avisos_llegan_mientras_se_navega(self):
        self.evento(inicio=timezone.now() + timedelta(minutes=10), minutos_aviso=15, titulo="Llamada importante")
        self.client.force_login(self.diego)
        self.client.get(reverse("inicio"))
        self.assertTrue(Notificacion.objects.filter(destinatario=self.diego, titulo__contains="Llamada importante").exists())

    def test_comando_para_programar(self):
        self.evento(inicio=timezone.now() + timedelta(minutes=10), minutos_aviso=15)
        call_command("enviar_avisos_agenda", verbosity=0)
        self.assertTrue(Notificacion.objects.filter(tipo="AGENDA").exists())

    def test_vencidos_y_proximos(self):
        vencido = self.evento(inicio=timezone.now() - timedelta(hours=2), titulo="Informe atrasado")
        hecho = self.evento(inicio=timezone.now() - timedelta(hours=3), titulo="Hecho")
        servicios.cambiar_estado(hecho, Evento.Estado.COMPLETADO)
        proximo = self.evento(inicio=timezone.now() + timedelta(days=2), titulo="Próximo")
        self.evento(inicio=timezone.now() + timedelta(days=20), titulo="Lejano")
        self.assertEqual(list(servicios.vencidos(self.diego)), [vencido])
        self.assertEqual(list(servicios.proximos(self.diego)), [proximo])
        self.client.force_login(self.diego)
        respuesta = self.client.get(reverse("agenda:calendario"))
        self.assertContains(respuesta, "Informe atrasado")
        self.assertContains(respuesta, "Vencidos sin completar")


class HorarioLaboralTests(BaseAgenda):
    """No se agenda fuera de la jornada laboral (lunes a viernes, 09:00 a 18:00)."""

    def datos(self, **cambios):
        return {"titulo": "Reunión", "tipo": "REUNION", "fecha": proximo_laborable().isoformat(),
                "hora_inicio": "10:00", "hora_fin": "11:00", "minutos_aviso": "15"} | cambios

    def errores(self, **cambios):
        self.client.force_login(self.diego)
        respuesta = self.client.post(reverse("agenda:crear"), self.datos(**cambios))
        return respuesta.context["form"].errors if respuesta.context else {}

    def test_dentro_del_horario_se_guarda(self):
        self.assertEqual(self.errores(hora_inicio="09:00", hora_fin="18:00"), {})
        self.assertEqual(Evento.objects.count(), 1)

    def test_antes_de_las_9_o_despues_de_las_18_no(self):
        self.assertIn("Fuera del horario laboral", self.errores(hora_inicio="08:30", hora_fin="10:00")["hora_inicio"][0])
        self.assertIn("Fuera del horario laboral", self.errores(hora_inicio="18:00", hora_fin="")["hora_inicio"][0])
        self.assertIn("a más tardar a las 18:00", self.errores(hora_inicio="17:30", hora_fin="18:30")["hora_fin"][0])
        self.assertEqual(Evento.objects.count(), 0)

    def test_fin_de_semana_no(self):
        sabado = proximo_laborable()
        while sabado.isoweekday() != 6:
            sabado += timedelta(days=1)
        self.assertIn("no es laborable", self.errores(fecha=sabado.isoformat())["fecha"][0])
        self.assertIn("no es laborable", self.errores(fecha=sabado.isoformat(), todo_el_dia="on")["fecha"][0])

    def test_el_formulario_limita_las_horas_y_muestra_la_jornada(self):
        self.client.force_login(self.diego)
        respuesta = self.client.get(reverse("agenda:crear"))
        self.assertContains(respuesta, 'min="09:00"')
        self.assertContains(respuesta, 'max="18:00"')
        self.assertContains(respuesta, "de lunes a viernes, de 09:00 a 18:00")

    def test_jornada_estandar_de_asistencia_es_de_9_a_18(self):
        from apps.asistencia.forms import HorarioForm
        from apps.asistencia.models import Horario

        estandar = Horario.objects.get(nombre="Jornada estándar")
        self.assertEqual((estandar.hora_entrada, estandar.hora_salida, estandar.dias_laborables), (time(9), time(18), [1, 2, 3, 4, 5]))
        form = HorarioForm()
        self.assertEqual((form.initial["hora_entrada"], form.initial["hora_salida"]), (time(9), time(18)))


class AccesoTests(BaseAgenda):
    def test_todos_los_roles_tienen_agenda_e_indicador(self):
        for usuario in [self.diego, self.lucia, self.ana]:
            with self.subTest(usuario=usuario.username):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(reverse("agenda:calendario")).status_code, 200)
                self.assertContains(self.client.get(reverse("inicio")), "Mis actividades de hoy")

    def test_requiere_sesion(self):
        self.assertEqual(self.client.get(reverse("agenda:calendario")).status_code, 302)
