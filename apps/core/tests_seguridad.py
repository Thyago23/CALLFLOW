"""
Seguridad de escritura y acceso: concurrencia optimista, cada usuario ve solo lo suyo, validación de lo que llega
en la petición y bloqueo de cuentas también por /admin/.
"""
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.chips.models import Chip
from apps.ventas.models import BaseComercial, FormularioDAMI
from apps.ventas.tests import BaseVentas

from .concurrencia import CAMPO
from .models import Notificacion, Rol, Usuario


def datos_de(form) -> dict:
    """Lo que el navegador enviaría con el formulario tal como se mostró."""
    datos = {}
    for campo in form:
        valor = campo.value()
        if valor is None or campo.field.disabled:
            continue
        datos[campo.html_name] = "on" if valor is True else ("" if valor is False else valor)
    return datos


# --------------------------------------------------------------------------- concurrencia optimista (huella)
class ConcurrenciaHuellaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.coordinador = Usuario.objects.create_user("marlon.rosero", password="x", rol=Rol.ENCARGADO_CIERRE)
        cls.coordinador.user_permissions.add(Permission.objects.get(content_type__app_label="chips", codename="gestionar_chips"))

    def setUp(self):
        self.client.force_login(self.coordinador)
        self.chip = Chip.objects.create(numero="0991234567", registrado_por=self.coordinador, observacion="original")
        self.url = reverse("chips:editar", args=[self.chip.pk])

    def abrir(self) -> dict:
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, f'name="{CAMPO}"')
        return datos_de(respuesta.context["form"])

    def test_dos_personas_editan_a_la_vez_y_la_segunda_no_pisa_a_la_primera(self):
        de_ana, de_luis = self.abrir(), self.abrir()
        self.assertEqual(self.client.post(self.url, de_ana | {"observacion": "cambio de Ana"}).status_code, 302)

        respuesta = self.client.post(self.url, de_luis | {"observacion": "cambio de Luis"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Otra persona modificó este registro")
        self.assertContains(respuesta, "cambio de Ana")  # le dice qué valor dejó la otra persona
        self.chip.refresh_from_db()
        self.assertEqual(self.chip.observacion, "cambio de Ana")

        # Ya vio el cambio: si guarda de nuevo desde el formulario devuelto, se guarda.
        otra_vez = datos_de(respuesta.context["form"])
        self.assertEqual(self.client.post(self.url, otra_vez).status_code, 302)
        self.chip.refresh_from_db()
        self.assertEqual(self.chip.observacion, "cambio de Luis")

    def test_sin_cambios_ajenos_guarda_normal(self):
        datos = self.abrir()
        self.assertEqual(self.client.post(self.url, datos | {"observacion": "nueva"}).status_code, 302)
        self.chip.refresh_from_db()
        self.assertEqual(self.chip.observacion, "nueva")

    def test_cambio_en_un_campo_que_el_formulario_no_edita_no_es_conflicto(self):
        datos = self.abrir()
        Chip.objects.filter(pk=self.chip.pk).update(registrado_por=None)
        self.assertEqual(self.client.post(self.url, datos | {"observacion": "nueva"}).status_code, 302)

    def test_huella_manipulada_o_ausente_no_guarda(self):
        datos = self.abrir()
        for huella in ("", "inventada", signing.dumps({"observacion": "x"}, salt="otra")):
            with self.subTest(huella=huella):
                respuesta = self.client.post(self.url, datos | {CAMPO: huella, "observacion": "intruso"})
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, "Recargue la página")
        self.chip.refresh_from_db()
        self.assertEqual(self.chip.observacion, "original")


# --------------------------------------------------------------------------- DAMI: version + cada vendedor ve lo suyo
class DAMISeguridadTests(BaseVentas):
    def setUp(self):
        self.crear()  # borrador de Lucía
        self.dami = FormularioDAMI.objects.get()
        self.url = reverse("ventas:formulario_editar", args=[self.dami.pk])

    def test_version_vieja_no_pisa_los_cambios(self):
        self.client.force_login(self.lucia)
        vieja = datos_de(self.client.get(self.url).context["form"])
        self.assertTrue(vieja.get("version"))
        self.client.post(self.url, vieja | {"observaciones": "primera pestaña"})
        respuesta = self.client.post(self.url, vieja | {"observaciones": "segunda pestaña"})
        self.assertContains(respuesta, "Otra persona guardó cambios")
        self.dami.refresh_from_db()
        self.assertEqual(self.dami.observaciones, "primera pestaña")

    def test_otro_vendedor_no_ve_ni_edita_el_dami_ajeno(self):
        self.client.force_login(self.pedro)
        for url in (reverse("ventas:formulario_detalle", args=[self.dami.pk]), self.url):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(self.url, {"nombres": "X", "version": self.dami.version}).status_code, 404)
        acciones = reverse("ventas:formulario_accion", args=[self.dami.pk])
        self.assertEqual(self.client.post(acciones, {"accion": "eliminar"}).status_code, 404)

    def test_vendedor_no_entra_a_lo_del_administrador(self):
        base = BaseComercial.objects.create(nombre="Base prueba")
        self.client.force_login(self.pedro)
        for url in (reverse("ventas:base_excel", args=[base.pk]), reverse("ventas:hoja_vendedor", args=[self.lucia.empleado.pk]),
                    reverse("ventas:bases_seguimiento"), reverse("ventas:hojas_vendedores")):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_notificacion_ajena_no_se_abre(self):
        n = Notificacion.objects.create(destinatario=self.lucia, titulo="Privada", mensaje="solo Lucía")
        self.client.force_login(self.pedro)
        self.assertEqual(self.client.get(reverse("notificacion_abrir", args=[n.pk])).status_code, 404)
        n.refresh_from_db()
        self.assertFalse(n.leida)

    def test_ids_basura_no_rompen_el_servidor(self):
        self.client.force_login(self.admin)
        for valor in ("abc", "1;DROP TABLE x", "-1", "9" * 40):
            with self.subTest(valor=valor):
                self.assertEqual(self.client.get(reverse("ventas:bases_seguimiento"), {"base": valor, "vendedor": valor}).status_code, 200)


# --------------------------------------------------------------------------- bloqueo también por /admin/
@override_settings(LIMITE_PETICIONES_ACTIVO=True, LIMITE_FALLOS_INGRESO=(3, 900, 900),
                   LIMITE_PETICIONES={"ingreso": (100, 300), "sondeo": (100, 60), "general": (100, 60)})
class BloqueoPorAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.jefe = Usuario.objects.create_superuser("jefe.callflow", password="Clave-Segura-2026")

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_admin_tambien_bloquea_la_cuenta(self):
        url = reverse("admin:login")
        for _ in range(3):
            self.client.post(url, {"username": "jefe.callflow", "password": "mal", "next": "/admin/"})
        self.client.post(url, {"username": "jefe.callflow", "password": "Clave-Segura-2026", "next": "/admin/"})
        self.assertNotIn("_auth_user_id", self.client.session)
        # Y en la pantalla normal de CALLFLOW tampoco.
        respuesta = self.client.post(reverse("login"), {"username": "jefe.callflow", "password": "Clave-Segura-2026"})
        self.assertContains(respuesta, "Demasiados intentos fallidos")
