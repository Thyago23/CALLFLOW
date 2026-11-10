"""Notificaciones: panel de la campana, avisos flotantes, página y push del navegador."""
import json
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import push
from .management.commands.generar_claves_push import nuevas_claves
from .models import Notificacion, Rol, SuscripcionPush, Usuario

PUBLICA, PRIVADA = None, None


def claves():
    global PUBLICA, PRIVADA
    if PRIVADA is None:
        PRIVADA, PUBLICA = nuevas_claves()
    return {"VAPID_PUBLIC_KEY": PUBLICA, "VAPID_PRIVATE_KEY": PRIVADA, "VAPID_CONTACTO": "mailto:prueba@callflow.test"}


SUSCRIPCION = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "keys": {"p256dh": "BPk" + "x" * 84, "auth": "a" * 22}}


class BaseNotificaciones(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.lucia = Usuario.objects.create_user("lucia.mora", None, "x", first_name="Lucía", rol=Rol.VENDEDOR)
        cls.pedro = Usuario.objects.create_user("pedro.paz", None, "x", first_name="Pedro", rol=Rol.VENDEDOR)

    def avisar(self, usuario=None, **datos):
        return Notificacion.objects.create(destinatario=usuario or self.lucia, titulo=datos.pop("titulo", "Resultado de DAMI-000001"),
                                           mensaje=datos.pop("mensaje", "Venta cerrada por $500"), **datos)

    def post_json(self, nombre, datos):
        return self.client.post(reverse(nombre), json.dumps(datos), content_type="application/json")


class CentroNotificacionesTests(BaseNotificaciones):
    def test_panel_de_la_campana_con_icono_por_tipo(self):
        self.avisar(tipo=Notificacion.Tipo.STOCK_BAJO, titulo="Stock bajo: resmas")
        self.client.force_login(self.lucia)
        panel = self.client.get(reverse("notificaciones_panel"))
        self.assertContains(panel, "Stock bajo: resmas")
        self.assertContains(panel, "bi-box-seam")
        self.assertContains(panel, "tono-alerta")
        self.assertContains(panel, "data-estado-push")  # aquí se activa el push
        self.assertContains(panel, reverse("notificaciones"))  # «Ver todas» lleva a la página
        inicio = self.client.get(reverse("inicio"))
        self.assertContains(inicio, 'data-bs-toggle="dropdown"')
        self.assertContains(inicio, reverse("notificaciones_panel"))
        self.assertContains(inicio, "js/notificaciones.js")

    def test_pagina_con_filtro_sin_leer(self):
        self.avisar(titulo="Nueva")
        leida = self.avisar(titulo="Vieja")
        Notificacion.objects.filter(pk=leida.pk).update(leida=True, leida_en=timezone.now())
        self.client.force_login(self.lucia)
        todas = self.client.get(reverse("notificaciones"))
        self.assertContains(todas, "Nueva")
        self.assertContains(todas, "Vieja")
        self.assertContains(todas, "Hoy")
        sin_leer = self.client.get(reverse("notificaciones"), {"ver": "sin_leer"})
        self.assertContains(sin_leer, "Nueva")
        self.assertNotContains(sin_leer, "Vieja")

    def test_marcar_todas_actualiza_el_contador(self):
        self.avisar()
        self.client.force_login(self.lucia)
        respuesta = self.client.post(reverse("notificaciones_leidas"), HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, 'id="contador-notificaciones" hx-swap-oob="innerHTML"')
        self.assertFalse(Notificacion.objects.filter(leida=False).exists())


class AvisosFlotantesTests(BaseNotificaciones):
    def test_el_aviso_flotante_usa_el_icono_del_tipo(self):
        """El ícono lo da el modelo: los avisos flotantes no deben intentar asignarlo (es una propiedad)."""
        self.avisar(titulo="Se le asignó DAMI-000002", tipo=Notificacion.Tipo.FORMULARIO_DAMI)
        self.client.force_login(self.lucia)
        respuesta = self.client.get(reverse("contadores"))
        self.assertContains(respuesta, "Se le asignó DAMI-000002")
        self.assertContains(respuesta, "bi-file-earmark-text")


class PushTests(BaseNotificaciones):
    def test_suscribir_reasignar_y_desuscribir(self):
        self.client.force_login(self.lucia)
        self.assertEqual(self.post_json("push_suscribir", SUSCRIPCION).status_code, 200)
        self.assertEqual(SuscripcionPush.objects.get().usuario, self.lucia)
        self.client.force_login(self.pedro)  # mismo navegador, otra persona
        self.post_json("push_suscribir", SUSCRIPCION)
        self.assertEqual(SuscripcionPush.objects.get().usuario, self.pedro)
        self.client.force_login(self.lucia)
        self.post_json("push_desuscribir", {"endpoint": SUSCRIPCION["endpoint"]})
        self.assertTrue(SuscripcionPush.objects.exists())  # no puede borrar la de otra persona
        self.client.force_login(self.pedro)
        self.post_json("push_desuscribir", {"endpoint": SUSCRIPCION["endpoint"]})
        self.assertFalse(SuscripcionPush.objects.exists())

    def test_rechaza_suscripciones_invalidas(self):
        self.client.force_login(self.lucia)
        for datos in ({}, {"endpoint": "http://inseguro/x", "keys": SUSCRIPCION["keys"]}, {"endpoint": SUSCRIPCION["endpoint"]}):
            self.assertEqual(self.post_json("push_suscribir", datos).status_code, 400)
        self.assertEqual(self.client.post(reverse("push_suscribir"), "no es json", content_type="application/json").status_code, 400)

    def test_service_worker_desde_la_raiz(self):
        respuesta = self.client.get(reverse("service_worker"))
        self.assertEqual(respuesta["Content-Type"], "application/javascript; charset=utf-8")
        self.assertEqual(respuesta["Service-Worker-Allowed"], "/")
        self.assertIn(b"showNotification", respuesta.content)

    def test_sin_claves_no_envia_nada(self):
        self.avisar()
        with override_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY=""), mock.patch("pywebpush.webpush") as webpush:
            self.assertEqual(push.enviar_pendientes(), 0)
        webpush.assert_not_called()

    def test_envia_una_vez_a_cada_navegador_del_destinatario(self):
        SuscripcionPush.objects.create(usuario=self.lucia, **{"endpoint": SUSCRIPCION["endpoint"], **SUSCRIPCION["keys"]})
        SuscripcionPush.objects.create(usuario=self.pedro, endpoint="https://otro/1", p256dh="x", auth="y")
        n = self.avisar(url="/ventas/")
        with override_settings(**claves()), mock.patch("pywebpush.webpush") as webpush:
            self.assertEqual(push.enviar_pendientes(), 1)
            self.assertEqual(push.enviar_pendientes(), 0)  # ya enviada: no se repite
        datos = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(webpush.call_args.kwargs["subscription_info"]["endpoint"], SUSCRIPCION["endpoint"])
        self.assertEqual((datos["title"], datos["url"]), ("Resultado de DAMI-000001", reverse("notificacion_abrir", args=[n.pk])))
        n.refresh_from_db()
        self.assertTrue(n.push_enviada)

    def test_las_viejas_no_se_envian_y_las_suscripciones_vencidas_se_borran(self):
        from pywebpush import WebPushException

        SuscripcionPush.objects.create(usuario=self.lucia, endpoint="https://vencida/1", p256dh="x", auth="y")
        vieja = self.avisar()
        Notificacion.objects.filter(pk=vieja.pk).update(creado_en=timezone.now() - timedelta(hours=2))
        self.avisar(titulo="Reciente")
        vencida = WebPushException("Gone", response=mock.Mock(status_code=410))
        with override_settings(**claves()), mock.patch("pywebpush.webpush", side_effect=vencida) as webpush:
            push.enviar_pendientes()
        self.assertEqual(webpush.call_count, 1)  # solo la reciente
        self.assertFalse(SuscripcionPush.objects.exists())
        self.assertFalse(Notificacion.objects.filter(push_enviada=False).exists())

    def test_las_del_trigger_de_stock_tambien_quedan_pendientes(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO core_notificacion (destinatario_id, tipo, titulo, mensaje, url, leida, creado_en) "
                           "VALUES (%s, 'STOCK_BAJO', 'Stock bajo: x', '', '', false, now())", [self.lucia.pk])
        self.assertTrue(Notificacion.objects.get().push_enviada is False)

    def test_comando_para_el_programador_de_tareas(self):
        with override_settings(**claves()), mock.patch("apps.core.push.enviar_pendientes", return_value=3) as enviar:
            call_command("enviar_push", verbosity=0)
        enviar.assert_called_once()
