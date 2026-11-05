"""
Asistencia: SCRUM-18 entradas/salidas, SCRUM-19 atrasos y ausencias,
SCRUM-20 clasificación justificada / no justificada, SCRUM-21 historial.
"""
import shutil
import tempfile
from datetime import date, datetime, time

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Rol, Usuario
from apps.documentos.models import Documento
from apps.personal.models import Cargo, Empleado

from . import servicios
from .models import AsignacionHorario, Horario, NovedadAsistencia, RegistroAsistencia

LUNES = date(2026, 11, 2)
SABADO = date(2026, 10, 31)
Tipo, Estado = NovedadAsistencia.Tipo, NovedadAsistencia.Estado


def respaldo(empleado):
    """Documento de respaldo ya guardado (para pruebas de servicios, sin subir un archivo real)."""
    import secrets

    from apps.documentos.models import TipoDocumento

    return Documento.objects.create(
        empleado=empleado, tipo=TipoDocumento.objects.get(codigo="SOPORTE_ASISTENCIA"), archivo="pruebas/certificado.pdf",
        nombre_original="certificado.pdf", tipo_mime="application/pdf", tamano_bytes=100, hash_sha256=secrets.token_hex(32),
    )


def a_las(dia: date, hh: int, mm: int):
    return timezone.make_aware(datetime.combine(dia, time(hh, mm)), timezone.get_current_timezone())


class BaseAsistencia(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.cargo = Cargo.objects.first()
        cls.horario = Horario.objects.create(nombre="Oficina", hora_entrada=time(8), hora_salida=time(17), minutos_tolerancia=5)
        cls.ana = cls.empleado("1710034065", "Ana", "Torres")
        cls.luis = cls.empleado("0926687856", "Luis", "Vega")
        cls.sin_horario = cls.empleado("1712345675", "Eva", "Ríos", horario=False)

    @classmethod
    def empleado(cls, cedula, nombres, apellidos, horario=True):
        e = Empleado.objects.create(identificacion=cedula, nombres=nombres, apellidos=apellidos, cargo=cls.cargo,
                                    fecha_ingreso=date(2026, 1, 1), telefono="0991234567")
        if horario:
            AsignacionHorario.objects.create(empleado=e, horario=cls.horario, vigente_desde=date(2026, 1, 1))
        return e

    def novedad(self, empleado, tipo, dia=LUNES):
        return NovedadAsistencia.objects.filter(empleado=empleado, tipo=tipo, fecha=dia).first()


class EntradasSalidasTests(BaseAsistencia):
    """SCRUM-18"""

    def test_registra_entrada_y_salida(self):
        servicios.registrar_entrada(self.ana, a_las(LUNES, 7, 55))
        registro = servicios.registrar_salida(self.ana, a_las(LUNES, 17, 5))
        self.assertEqual(registro.fecha, LUNES)
        self.assertEqual(servicios.horas_trabajadas(registro).total_seconds(), 9 * 3600 + 10 * 60)
        self.assertFalse(NovedadAsistencia.objects.exists())

    def test_una_entrada_por_dia_y_salida_requiere_entrada(self):
        with self.assertRaisesMessage(ValidationError, "no tiene entrada registrada"):
            servicios.registrar_salida(self.ana, a_las(LUNES, 17, 0))
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 0))
        with self.assertRaisesMessage(ValidationError, "ya tiene la entrada"):
            servicios.registrar_entrada(self.ana, a_las(LUNES, 9, 0))
        servicios.registrar_salida(self.ana, a_las(LUNES, 17, 0))
        with self.assertRaisesMessage(ValidationError, "ya tiene la salida"):
            servicios.registrar_salida(self.ana, a_las(LUNES, 18, 0))

    def test_empleado_no_activo_no_marca(self):
        self.ana.estado = Empleado.Estado.VACACIONES
        self.ana.save()
        with self.assertRaisesMessage(ValidationError, "no está activo"):
            servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 0))


class AtrasosAusenciasTests(BaseAsistencia):
    """SCRUM-19"""

    def test_dentro_de_la_tolerancia_no_es_atraso(self):
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 5))
        self.assertIsNone(self.novedad(self.ana, Tipo.ATRASO))

    def test_atraso_con_minutos_desde_la_hora_de_entrada(self):
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 12))
        atraso = self.novedad(self.ana, Tipo.ATRASO)
        self.assertEqual((atraso.minutos, atraso.estado), (12, Estado.PENDIENTE))

    def test_salida_anticipada(self):
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 0))
        servicios.registrar_salida(self.ana, a_las(LUNES, 16, 30))
        self.assertEqual(self.novedad(self.ana, Tipo.SALIDA_ANTICIPADA).minutos, 30)

    def test_dia_no_laborable_no_genera_atraso(self):
        servicios.registrar_entrada(self.ana, a_las(SABADO, 10, 0))
        self.assertIsNone(self.novedad(self.ana, Tipo.ATRASO, SABADO))

    def test_correccion_manual_recalcula_solo_lo_pendiente(self):
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 30))
        servicios.registrar_manual(self.ana, LUNES, time(8, 2), time(17, 0))
        self.assertIsNone(self.novedad(self.ana, Tipo.ATRASO))  # el olvido se corrigió: ya no hay atraso

        servicios.registrar_manual(self.ana, LUNES, time(8, 40), time(17, 0))
        servicios.clasificar(self.novedad(self.ana, Tipo.ATRASO), Estado.NO_JUSTIFICADA, "", None)
        servicios.registrar_manual(self.ana, LUNES, time(8, 0), time(17, 0))
        self.assertEqual(self.novedad(self.ana, Tipo.ATRASO).estado, Estado.NO_JUSTIFICADA)  # lo clasificado no se toca

    def test_marcar_ausencias_del_dia(self):
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 0))
        self.assertEqual(servicios.marcar_ausencias_del_dia(LUNES), 1)  # solo Luis (Eva no tiene horario)
        self.assertIsNotNone(self.novedad(self.luis, Tipo.AUSENCIA))
        self.assertEqual(servicios.marcar_ausencias_del_dia(LUNES), 0)  # no duplica
        self.assertEqual(servicios.marcar_ausencias_del_dia(SABADO), 0)  # fin de semana
        with self.assertRaisesMessage(ValidationError, "futuros"):
            servicios.marcar_ausencias_del_dia(timezone.localdate().replace(year=2099))

    def test_llegar_tarde_quita_la_ausencia_pendiente(self):
        servicios.marcar_ausencia(self.luis, LUNES)
        servicios.registrar_entrada(self.luis, a_las(LUNES, 10, 0))
        self.assertIsNone(self.novedad(self.luis, Tipo.AUSENCIA))
        self.assertEqual(self.novedad(self.luis, Tipo.ATRASO).minutos, 120)

    def test_ausencia_clasificada_bloquea_registrar_asistencia(self):
        ausencia = servicios.marcar_ausencia(self.luis, LUNES)
        servicios.clasificar(ausencia, Estado.JUSTIFICADA, "Cita médica", None, respaldo(self.luis))
        with self.assertRaisesMessage(ValidationError, "ya clasificada"):
            servicios.registrar_entrada(self.luis, a_las(LUNES, 9, 0))

    def test_no_se_marca_ausente_a_quien_vino(self):
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 0))
        with self.assertRaisesMessage(ValidationError, "tiene asistencia registrada"):
            servicios.marcar_ausencia(self.ana, LUNES)


class ClasificacionTests(BaseAsistencia):
    """SCRUM-20"""

    def setUp(self):
        self.ausencia = servicios.marcar_ausencia(self.luis, LUNES)
        self.admin = Usuario.objects.create_user("gabo.admin", None, "x", rol=Rol.ADMINISTRADOR)

    def test_justificada_exige_motivo(self):
        with self.assertRaisesMessage(ValidationError, "motivo"):
            servicios.clasificar(self.ausencia, Estado.JUSTIFICADA, "  ", self.admin)

    def test_justificada_exige_archivo(self):
        with self.assertRaisesMessage(ValidationError, "debe adjuntar el archivo"):
            servicios.clasificar(self.ausencia, Estado.JUSTIFICADA, "Cita médica", self.admin)
        self.ausencia.refresh_from_db()
        self.assertEqual(self.ausencia.estado, Estado.PENDIENTE)

    def test_no_justificada_no_exige_archivo(self):
        servicios.clasificar(self.ausencia, Estado.NO_JUSTIFICADA, "", self.admin)
        self.assertEqual(self.ausencia.estado, Estado.NO_JUSTIFICADA)

    def test_la_base_de_datos_tambien_exige_el_archivo(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            NovedadAsistencia.objects.filter(pk=self.ausencia.pk).update(
                estado=Estado.JUSTIFICADA, motivo="x", revisada_en=timezone.now())

    def test_clasificar_y_reabrir_conserva_el_archivo(self):
        servicios.clasificar(self.ausencia, Estado.JUSTIFICADA, "Calamidad doméstica", self.admin, respaldo(self.luis))
        self.ausencia.refresh_from_db()
        self.assertEqual((self.ausencia.estado, self.ausencia.revisada_por), (Estado.JUSTIFICADA, self.admin))
        self.assertIsNotNone(self.ausencia.revisada_en)
        servicios.reabrir(self.ausencia)
        self.ausencia.refresh_from_db()
        self.assertEqual(self.ausencia.estado, Estado.PENDIENTE)
        self.assertIsNone(self.ausencia.revisada_en)
        # Al volver a justificarla no hace falta subir de nuevo el archivo que ya tenía.
        servicios.clasificar(self.ausencia, Estado.JUSTIFICADA, "Calamidad doméstica", self.admin)
        self.assertEqual(self.ausencia.estado, Estado.JUSTIFICADA)


class RecordEmpleadoTests(BaseAsistencia):
    """Ficha de cada empleado: faltas, atrasos y cuántos fueron justificados."""

    def setUp(self):
        self.admin = Usuario.objects.create_user("gabo.admin", None, "x", rol=Rol.ADMINISTRADOR)
        martes, miercoles, jueves = date(2026, 11, 3), date(2026, 11, 4), date(2026, 10, 1)
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 20))                     # atraso 20, justificado
        servicios.registrar_entrada(self.ana, a_las(martes, 8, 15))                    # atraso 15, no justificado
        servicios.registrar_entrada(self.ana, a_las(jueves, 8, 30))                    # atraso 30, pendiente (octubre)
        servicios.clasificar(self.novedad(self.ana, Tipo.ATRASO), Estado.JUSTIFICADA, "Tráfico por accidente", self.admin, respaldo(self.ana))
        servicios.clasificar(self.novedad(self.ana, Tipo.ATRASO, martes), Estado.NO_JUSTIFICADA, "", self.admin)
        servicios.clasificar(servicios.marcar_ausencia(self.ana, miercoles), Estado.JUSTIFICADA, "Cita médica", self.admin, respaldo(self.ana))
        servicios.marcar_ausencia(self.ana, date(2026, 10, 2))                          # falta pendiente (octubre)
        servicios.marcar_ausencia(self.luis, LUNES)                                     # de otra persona: no cuenta

    def test_resumen_de_toda_su_historia(self):
        r = servicios.resumen_empleado(self.ana)
        self.assertEqual(r["faltas"], {"total": 2, "justificadas": 1, "no_justificadas": 0, "pendientes": 1, "minutos": 0})
        self.assertEqual(r["atrasos"], {"total": 3, "justificadas": 1, "no_justificadas": 1, "pendientes": 1, "minutos": 65})
        self.assertEqual(r["dias_asistidos"], 3)
        self.assertEqual(len(r["novedades"]), 5)

    def test_resumen_por_periodo(self):
        r = servicios.resumen_empleado(self.ana, date(2026, 11, 1), date(2026, 11, 30))
        self.assertEqual((r["faltas"]["total"], r["atrasos"]["total"], r["atrasos"]["minutos"]), (1, 2, 35))

    def test_pagina_del_empleado(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("personal:empleado_detalle", args=[self.ana.pk]))
        self.assertContains(respuesta, "Récord de asistencia")
        self.assertEqual(respuesta.context["asistencia"]["faltas"]["justificadas"], 1)
        self.assertContains(respuesta, "Tráfico por accidente")
        self.assertContains(respuesta, "certificado.pdf")
        # Desde la lista del personal se llega a la ficha.
        self.assertContains(self.client.get(reverse("personal:empleado_lista")), reverse("personal:empleado_detalle", args=[self.ana.pk]))

    def test_vendedor_no_ve_la_ficha(self):
        vendedor = Usuario.objects.create_user("vera.ventas", None, "x", rol=Rol.VENDEDOR)
        self.client.force_login(vendedor)
        self.assertEqual(self.client.get(reverse("personal:empleado_detalle", args=[self.ana.pk])).status_code, 403)


class HistorialTests(BaseAsistencia):
    """SCRUM-21"""

    def test_historial_por_empleado_y_rango_con_resumen(self):
        martes = date(2026, 11, 3)
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 20))
        servicios.registrar_salida(self.ana, a_las(LUNES, 17, 0))
        servicios.marcar_ausencia(self.ana, martes)
        servicios.registrar_entrada(self.luis, a_las(LUNES, 8, 0))

        filas, resumen = servicios.historial(LUNES, martes, self.ana)
        self.assertEqual([f["fecha"] for f in filas], [martes, LUNES])
        self.assertEqual(resumen["dias_trabajados"], 1)
        self.assertEqual((resumen["atrasos"], resumen["minutos_atraso"]), (1, 20))
        self.assertEqual(resumen["ausencias"], 1)
        self.assertEqual(resumen["pendientes"], 2)
        self.assertEqual(resumen["horas"].total_seconds(), 8 * 3600 + 40 * 60)

        _, todos = servicios.historial(LUNES, martes)
        self.assertEqual(todos["dias_trabajados"], 2)


class HorariosTests(BaseAsistencia):
    def test_nueva_asignacion_cierra_la_anterior(self):
        nocturno = Horario.objects.create(nombre="Tarde", hora_entrada=time(14), hora_salida=time(22))
        servicios.asignar_horario(nocturno, [self.ana], date(2026, 12, 1))
        anterior = AsignacionHorario.objects.get(empleado=self.ana, horario=self.horario)
        self.assertEqual(anterior.vigente_hasta, date(2026, 11, 30))
        self.assertEqual(servicios.horario_de(self.ana, date(2026, 11, 30)), self.horario)
        self.assertEqual(servicios.horario_de(self.ana, date(2026, 12, 1)), nocturno)
        with self.assertRaisesMessage(ValidationError, "ya tiene un horario asignado"):
            servicios.asignar_horario(self.horario, [self.ana], date(2026, 11, 15))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="callflow-pruebas-"))
class VistasTests(BaseAsistencia):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.admin = Usuario.objects.create_user("gabo.admin", None, "x", rol=Rol.ADMINISTRADOR)
        self.vendedor = Usuario.objects.create_user("vera.ventas", None, "x", rol=Rol.VENDEDOR)

    def test_vendedor_no_accede(self):
        self.client.force_login(self.vendedor)
        for nombre in ["asistencia:tablero", "asistencia:novedades", "asistencia:historial", "asistencia:horarios"]:
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)
        self.assertEqual(self.client.post(reverse("asistencia:entrada", args=[self.ana.pk])).status_code, 403)

    def test_tablero_y_marcacion_desde_la_pantalla(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("asistencia:tablero"))
        self.assertContains(respuesta, "Torres Ana")
        self.assertContains(respuesta, "Sin horario")
        respuesta = self.client.post(reverse("asistencia:entrada", args=[self.ana.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Entrada de Ana Torres")
        self.assertNotContains(respuesta, "<html")
        self.assertTrue(RegistroAsistencia.objects.filter(empleado=self.ana, fecha=timezone.localdate()).exists())
        respuesta = self.client.post(reverse("asistencia:entrada", args=[self.ana.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "ya tiene la entrada registrada")

    def test_registro_manual(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("asistencia:registro_manual"), {
            "empleado": self.luis.pk, "fecha": LUNES.isoformat(), "hora_entrada": "08:25", "hora_salida": "17:00",
            "observacion": "Olvidó marcar",
        })
        self.assertRedirects(respuesta, f"{reverse('asistencia:tablero')}?fecha={LUNES.isoformat()}")
        self.assertEqual(self.novedad(self.luis, Tipo.ATRASO).minutos, 25)
        respuesta = self.client.post(reverse("asistencia:registro_manual"), {
            "empleado": self.luis.pk, "fecha": "2099-01-01", "hora_entrada": "08:00"})
        self.assertIn("fecha", respuesta.context["form"].errors)

    def test_clasificar_con_soporte_pdf(self):
        ausencia = servicios.marcar_ausencia(self.luis, LUNES)
        self.client.force_login(self.admin)
        pdf = SimpleUploadedFile("certificado.pdf", b"%PDF-1.4\n%prueba\n", content_type="application/pdf")
        respuesta = self.client.post(reverse("asistencia:clasificar", args=[ausencia.pk]),
                                     {"estado": "JUSTIFICADA", "motivo": "Cita médica", "soporte": pdf}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Justificada")
        ausencia.refresh_from_db()
        self.assertEqual(ausencia.estado, Estado.JUSTIFICADA)
        self.assertEqual(ausencia.soporte.tipo_mime, "application/pdf")
        self.assertEqual(ausencia.soporte.empleado, self.luis)
        # El soporte solo lo ven usuarios con permiso.
        self.assertEqual(self.client.get(reverse("documentos:ver", args=[ausencia.soporte.pk])).status_code, 200)
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(reverse("documentos:ver", args=[ausencia.soporte.pk])).status_code, 403)

    def test_pantalla_no_deja_justificar_sin_archivo(self):
        ausencia = servicios.marcar_ausencia(self.luis, LUNES)
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("asistencia:clasificar", args=[ausencia.pk]),
                                     {"estado": "JUSTIFICADA", "motivo": "Cita médica"}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Para justificar debe adjuntar el archivo de respaldo")
        ausencia.refresh_from_db()
        self.assertEqual(ausencia.estado, Estado.PENDIENTE)
        # "No justificada" sí se puede guardar sin archivo.
        self.client.post(reverse("asistencia:clasificar", args=[ausencia.pk]), {"estado": "NO_JUSTIFICADA"}, HTTP_HX_REQUEST="true")
        ausencia.refresh_from_db()
        self.assertEqual(ausencia.estado, Estado.NO_JUSTIFICADA)

    def test_soporte_que_no_es_pdf_ni_imagen_se_rechaza(self):
        ausencia = servicios.marcar_ausencia(self.luis, LUNES)
        self.client.force_login(self.admin)
        falso = SimpleUploadedFile("certificado.pdf", b"MZ\x90\x00 ejecutable disfrazado", content_type="application/pdf")
        respuesta = self.client.post(reverse("asistencia:clasificar", args=[ausencia.pk]),
                                     {"estado": "JUSTIFICADA", "motivo": "x", "soporte": falso}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Solo se aceptan archivos PDF, JPG o PNG")
        ausencia.refresh_from_db()
        self.assertEqual(ausencia.estado, Estado.PENDIENTE)
        self.assertFalse(Documento.objects.exists())

    def test_crear_y_asignar_horario_desde_la_pantalla(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("asistencia:horarios"), {
            "crear_horario": "", "h-nombre": "Medio tiempo", "h-hora_entrada": "09:00", "h-hora_salida": "13:00",
            "h-minutos_tolerancia": "10", "h-dias_laborables": ["1", "3", "5"],
        })
        medio = Horario.objects.get(nombre="Medio tiempo")
        self.assertEqual(medio.dias_laborables, [1, 3, 5])
        respuesta = self.client.post(reverse("asistencia:horarios"), {
            "asignar": "", "a-horario": medio.pk, "a-empleados": [self.sin_horario.pk], "a-desde": "2026-11-01"}, follow=True)
        self.assertContains(respuesta, "asignado a 1 empleado")
        self.assertEqual(servicios.horario_de(self.sin_horario, LUNES), medio)

    def test_historial_y_novedades_cargan(self):
        servicios.registrar_entrada(self.ana, a_las(LUNES, 8, 30))
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("asistencia:historial"), {"empleado": self.ana.pk, "desde": "2026-11-01", "hasta": "2026-11-30"})
        self.assertContains(respuesta, "Atraso 30 min")
        self.assertContains(self.client.get(reverse("asistencia:novedades")), "Torres Ana")
        self.assertContains(self.client.get(reverse("inicio")), "Novedades de asistencia por revisar")
