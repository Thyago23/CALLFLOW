"""Mi perfil: foto, apodo interno y modo claro/oscuro."""
import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Rol, Tema, Usuario

MEDIA_PRUEBAS = tempfile.mkdtemp(prefix="callflow-perfil-")


def imagen(formato="PNG", tamano=(800, 500), nombre="foto.png"):
    salida = BytesIO()
    Image.new("RGB", tamano, (200, 40, 40)).save(salida, formato)
    return SimpleUploadedFile(nombre, salida.getvalue(), content_type=f"image/{formato.lower()}")


@override_settings(MEDIA_ROOT=MEDIA_PRUEBAS)
class PerfilTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_PRUEBAS, ignore_errors=True)

    def setUp(self):
        self.usuario = Usuario.objects.create_user("lucia.mora", password="Clave-Segura-2026", first_name="Lucía",
                                                   last_name="Mora", rol=Rol.VENDEDOR)
        self.client.force_login(self.usuario)

    def guardar(self, **datos):
        datos.setdefault("apodo", "")
        datos.setdefault("tema", Tema.CLARO)
        return self.client.post(reverse("perfil"), datos)

    def test_enlace_en_el_menu_y_pagina(self):
        inicio = self.client.get(reverse("inicio"))
        self.assertContains(inicio, reverse("perfil"))
        self.assertContains(inicio, 'data-bs-theme="light"')
        pagina = self.client.get(reverse("perfil"))
        self.assertContains(pagina, "Mi perfil")
        self.assertContains(pagina, "Modo de pantalla")

    def test_apodo_y_modo_oscuro(self):
        respuesta = self.guardar(apodo="  La   Jefa ", tema=Tema.OSCURO)
        self.assertRedirects(respuesta, reverse("perfil"))
        self.usuario.refresh_from_db()
        self.assertEqual((self.usuario.apodo, self.usuario.tema), ("La Jefa", Tema.OSCURO))
        inicio = self.client.get(reverse("inicio"))
        self.assertContains(inicio, 'data-bs-theme="dark"')
        self.assertContains(inicio, "La Jefa")
        self.assertContains(inicio, 'title="Lucía Mora"')  # el nombre real sigue a la vista al pasar el mouse
        self.assertEqual(self.usuario.iniciales, "LJ")

    def test_apodo_sin_letras_no_vale(self):
        respuesta = self.guardar(apodo="!!!")
        self.assertContains(respuesta, "al menos una letra o número")

    def test_tema_desconocido_no_vale(self):
        self.guardar(tema="ROSADO")
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.tema, Tema.CLARO)

    def test_subir_foto_se_recorta_y_se_sirve(self):
        self.guardar(foto_nueva=imagen())
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.foto.name.startswith("perfiles/"))
        with Image.open(self.usuario.foto.path) as guardada:
            self.assertEqual((guardada.format, guardada.size), ("JPEG", (320, 320)))
        foto = self.client.get(self.usuario.url_foto)
        self.assertEqual((foto.status_code, foto["Content-Type"]), (200, "image/jpeg"))
        self.assertContains(self.client.get(reverse("inicio")), self.usuario.url_foto)

    def test_cambiar_foto_borra_la_anterior_y_quitarla_tambien(self):
        self.guardar(foto_nueva=imagen())
        self.usuario.refresh_from_db()
        primera = self.usuario.foto.path
        self.guardar(foto_nueva=imagen("WEBP", nombre="otra.webp"))
        self.usuario.refresh_from_db()
        segunda = self.usuario.foto.path
        self.assertNotEqual(primera, segunda)
        self.assertFalse(Image.os.path.exists(primera))
        self.guardar(quitar_foto="on")
        self.usuario.refresh_from_db()
        self.assertFalse(self.usuario.foto)
        self.assertFalse(Image.os.path.exists(segunda))

    def test_archivo_que_no_es_imagen(self):
        falso = SimpleUploadedFile("foto.png", b"<script>alert(1)</script>", content_type="image/png")
        respuesta = self.guardar(foto_nueva=falso)
        self.assertContains(respuesta, "no es una imagen válida")
        self.usuario.refresh_from_db()
        self.assertFalse(self.usuario.foto)

    def test_foto_solo_con_sesion(self):
        self.guardar(foto_nueva=imagen())
        self.usuario.refresh_from_db()
        url = self.usuario.url_foto
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_solo_edita_su_propio_perfil(self):
        otro = Usuario.objects.create_user("pedro.paz", password="Clave-Segura-2026", rol=Rol.VENDEDOR)
        self.guardar(apodo="Lu")
        otro.refresh_from_db()
        self.assertEqual(otro.apodo, "")
