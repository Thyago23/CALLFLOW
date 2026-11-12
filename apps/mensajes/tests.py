"""
Mensajería interna: solo TI, administradores y supervisores; los asesores de ventas no tienen acceso.

    python manage.py test apps.mensajes
"""
import shutil
import tempfile

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import Rol, Usuario

from . import servicios
from .models import Conversacion, Mensaje, Participante

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00"
       b"\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="callflow-test-"))
class MensajeriaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear = Usuario.objects.create_user
        cls.admin = crear("admin.m", "a@callflow.test", "clave-segura-123", rol=Rol.ADMINISTRADOR, first_name="Ana")
        cls.ti = crear("ti.m", "t@callflow.test", "clave-segura-123", rol=Rol.TI, first_name="Tomás")
        cls.supervisor = crear("super.m", "s@callflow.test", "clave-segura-123", rol=Rol.ENCARGADO_CIERRE, first_name="Sara")
        cls.asesor = crear("asesor.m", "v@callflow.test", "clave-segura-123", rol=Rol.VENDEDOR, first_name="Vico")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def enviar(self, usuario, conversacion, **datos):
        self.client.force_login(usuario)
        return self.client.post(reverse("mensajes:enviar", args=[conversacion.pk]), datos, HTTP_HX_REQUEST="true")

    def test_los_asesores_no_tienen_acceso(self):
        self.client.force_login(self.asesor)
        self.assertEqual(self.client.get(reverse("mensajes:bandeja")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("inicio")), reverse("mensajes:bandeja"))
        self.assertNotIn(self.asesor, servicios.contactos(self.admin))
        for usuario in (self.admin, self.ti, self.supervisor):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("mensajes:bandeja")).status_code, 200)

    def test_conversacion_directa_unica_y_no_leidos(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("mensajes:nueva_directa"), {"usuario": self.ti.pk})
        self.client.post(reverse("mensajes:nueva_directa"), {"usuario": self.ti.pk})
        conversacion = Conversacion.objects.get()
        self.assertContains(self.enviar(self.admin, conversacion, texto="La PC de caja no enciende"), 'class="mensaje mio')
        self.assertEqual(servicios.sin_leer(self.ti), 1)
        self.client.force_login(self.ti)
        nuevos = self.client.get(reverse("mensajes:nuevos", args=[conversacion.pk]), {"despues": 0})
        self.assertContains(nuevos, "La PC de caja no enciende")
        self.assertEqual(servicios.sin_leer(self.ti), 0)
        ultimo = Mensaje.objects.latest("pk").pk
        self.assertEqual(self.client.get(reverse("mensajes:nuevos", args=[conversacion.pk]), {"despues": ultimo}).status_code, 204)
        self.client.force_login(self.supervisor)  # no participa
        self.assertEqual(self.client.get(reverse("mensajes:conversacion", args=[conversacion.pk])).status_code, 404)

    def test_grupo_adjuntos_y_eliminar(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("mensajes:nuevo_grupo"), {"nombre": "Soporte TI", "miembros": [self.ti.pk, self.supervisor.pk]})
        grupo = Conversacion.objects.get(nombre="Soporte TI")
        self.assertEqual(grupo.participantes.count(), 3)
        self.enviar(self.admin, grupo, texto="Captura", adjunto=SimpleUploadedFile("error.png", PNG, content_type="image/png"))
        mensaje = Mensaje.objects.get(conversacion=grupo)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse("mensajes:adjunto", args=[mensaje.pk])).status_code, 200)
        self.client.force_login(self.asesor)
        self.assertEqual(self.client.get(reverse("mensajes:adjunto", args=[mensaje.pk])).status_code, 403)
        respuesta = self.enviar(self.admin, grupo, adjunto=SimpleUploadedFile("x.pdf", b"MZ no es pdf", content_type="application/pdf"))
        self.assertContains(respuesta, "Adjunto")
        self.client.force_login(self.ti)
        self.client.post(reverse("mensajes:eliminar", args=[mensaje.pk]))
        mensaje.refresh_from_db()
        self.assertFalse(mensaje.eliminado)  # solo el autor elimina
        self.client.force_login(self.admin)
        self.client.post(reverse("mensajes:eliminar", args=[mensaje.pk]))
        mensaje.refresh_from_db()
        self.assertTrue(mensaje.eliminado)
        self.assertFalse(mensaje.adjunto)

    def test_la_base_impide_sumar_asesores_y_editar_mensajes(self):
        directa = servicios.conversacion_directa(self.admin, self.ti)
        grupo = servicios.crear_grupo(self.admin, "Equipo", [self.ti])
        servicios.enviar(self.admin, directa, "hola")
        intentos = [
            lambda: Participante.objects.create(conversacion=grupo, usuario=self.asesor),
            lambda: Mensaje.objects.create(conversacion=grupo, autor=self.asesor, texto="hola"),
            lambda: Mensaje.objects.create(conversacion=grupo, autor=self.supervisor, texto="no soy miembro"),
            lambda: Participante.objects.create(conversacion=directa, usuario=self.supervisor),
            lambda: Mensaje.objects.filter(conversacion=directa).update(texto="cambiado"),
            lambda: Mensaje.objects.filter(conversacion=directa).delete(),
        ]
        for intento in intentos:
            with self.assertRaises(DatabaseError), transaction.atomic():
                intento()

    def test_salir_del_grupo(self):
        grupo = servicios.crear_grupo(self.admin, "Equipo", [self.ti])
        self.client.force_login(self.ti)
        self.client.post(reverse("mensajes:salir", args=[grupo.pk]))
        self.assertEqual(self.client.get(reverse("mensajes:conversacion", args=[grupo.pk])).status_code, 404)
