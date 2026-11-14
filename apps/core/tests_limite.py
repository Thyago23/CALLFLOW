"""Límite de peticiones (rate limit) y bloqueo de cuentas por intentos fallidos de ingreso."""
import json
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.ti import registro_errores

from .models import Rol, Usuario

LIMITES_PRUEBA = {"ingreso": (3, 300), "sondeo": (4, 60), "general": (5, 60)}


@override_settings(LIMITE_PETICIONES_ACTIVO=True, LIMITE_PETICIONES=LIMITES_PRUEBA, LIMITE_FALLOS_INGRESO=(3, 900, 900))
class LimitePeticionesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lucia = Usuario.objects.create_user("lucia.mora", None, "LM48207153", first_name="Lucía", last_name="Mora",
                                                rol=Rol.VENDEDOR)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def eventos(self):
        archivo = Path(settings.LOGS_DIR) / registro_errores.ARCHIVO_SEGURIDAD
        if not archivo.exists():
            return []
        return [json.loads(l) for l in archivo.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_general_responde_429_al_pasar_el_limite(self):
        for _ in range(5):
            self.assertNotEqual(self.client.get(reverse("login")).status_code, 429)
        respuesta = self.client.get(reverse("login"))
        self.assertEqual(respuesta.status_code, 429)
        self.assertTrue(int(respuesta["Retry-After"]) >= 1)
        self.assertContains(respuesta, "Demasiadas solicitudes", status_code=429)

    def test_rechazo_no_consulta_la_base(self):
        for _ in range(5):
            self.client.get(reverse("login"))
        with CaptureQueriesContext(connection) as consultas:
            self.assertEqual(self.client.get(reverse("login")).status_code, 429)
        self.assertEqual(len(consultas), 0)

    def test_htmx_recibe_429_sin_cuerpo(self):
        self.client.force_login(self.lucia)
        for _ in range(4):
            self.client.get(reverse("contadores"), HTTP_HX_REQUEST="true")
        respuesta = self.client.get(reverse("contadores"), HTTP_HX_REQUEST="true")
        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.content, b"")

    def test_sondeo_y_general_se_cuentan_aparte(self):
        self.client.force_login(self.lucia)
        for _ in range(4):
            self.assertEqual(self.client.get(reverse("contadores")).status_code, 200)
        self.assertEqual(self.client.get(reverse("contadores")).status_code, 429)
        self.assertNotEqual(self.client.get(reverse("perfil")).status_code, 429)

    def test_cada_ip_tiene_su_propio_contador(self):
        for _ in range(6):
            self.client.get(reverse("login"), REMOTE_ADDR="10.0.0.1")
        self.assertNotEqual(self.client.get(reverse("login"), REMOTE_ADDR="10.0.0.2").status_code, 429)

    def test_estaticos_no_cuentan(self):
        for _ in range(10):
            self.client.get("/" + settings.STATIC_URL.lstrip("/") + "css/no-existe.css")
        self.assertNotEqual(self.client.get(reverse("login")).status_code, 429)

    def test_exceso_queda_registrado_una_vez_por_ventana(self):
        for _ in range(9):
            self.client.get(reverse("login"), REMOTE_ADDR="10.9.9.9")
        propios = [e for e in self.eventos() if e.get("evento") == "limite_peticiones" and e.get("ip") == "10.9.9.9"]
        self.assertEqual(len(propios), 1)
        self.assertEqual(propios[0]["estado_http"], 429)

    def test_cuenta_se_bloquea_tras_intentos_fallidos(self):
        datos = {"username": "lucia.mora", "password": "MAL"}
        for i in range(3):
            self.client.post(reverse("login"), datos, REMOTE_ADDR=f"10.1.0.{i}")  # IP distinta: no lo frena el límite por IP
        # Con el código correcto tampoco entra mientras dure el bloqueo.
        respuesta = self.client.post(reverse("login"), {"username": "lucia.mora", "password": "LM48207153"},
                                     REMOTE_ADDR="10.1.0.99")
        self.assertContains(respuesta, "Demasiados intentos fallidos")
        self.assertNotIn("_auth_user_id", self.client.session)
        tipos = [e["evento"] for e in self.eventos() if e.get("usuario_intentado") == "lucia.mora"]
        self.assertIn("ingreso_fallido", tipos)
        self.assertIn("ingreso_bloqueado", tipos)

    def test_ingreso_correcto_reinicia_los_fallos(self):
        self.client.post(reverse("login"), {"username": "lucia.mora", "password": "MAL"}, REMOTE_ADDR="10.2.0.1")
        self.client.post(reverse("login"), {"username": "lucia.mora", "password": "MAL"}, REMOTE_ADDR="10.2.0.2")
        self.client.post(reverse("login"), {"username": "lucia.mora", "password": "LM48207153"}, REMOTE_ADDR="10.2.0.3")
        self.client.logout()
        self.client.post(reverse("login"), {"username": "lucia.mora", "password": "MAL"}, REMOTE_ADDR="10.2.0.4")
        respuesta = self.client.post(reverse("login"), {"username": "lucia.mora", "password": "LM48207153"}, REMOTE_ADDR="10.2.0.5")
        self.assertRedirects(respuesta, reverse("inicio"))

    def test_login_por_ip_frena_fuerza_bruta(self):
        for i in range(3):
            self.client.post(reverse("login"), {"username": f"x{i}", "password": "MAL"})
        self.assertEqual(self.client.post(reverse("login"), {"username": "y", "password": "MAL"}).status_code, 429)

    def test_eventos_aparecen_en_ti_servidor(self):
        self.client.post(reverse("login"), {"username": "lucia.mora", "password": "MAL"}, REMOTE_ADDR="10.3.0.1")
        grupos = registro_errores.eventos_seguridad(1)
        self.assertTrue(any(g["evento"] == "ingreso_fallido" and g["ip"] == "10.3.0.1" for g in grupos))
