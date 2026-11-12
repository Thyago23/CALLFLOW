"""TI › Servidor: espacio en disco, tamaño de la base, último respaldo y errores recientes del sistema."""
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import DatabaseError
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from . import registro_errores, servidor
from .tests import BaseTI


class ServidorTests(BaseTI):
    def setUp(self):
        super().setUp()
        self.respaldos = Path(tempfile.mkdtemp(prefix="callflow-respaldos-"))
        self.addCleanup(shutil.rmtree, self.respaldos, ignore_errors=True)

    def registrar_error(self, mensaje, ruta="/ventas/bases/"):
        """Un error como lo registra Django al responder 500, con la traza y la petición."""
        peticion = RequestFactory().get(ruta)
        peticion.user = self.vendedor
        try:
            raise ValueError(mensaje)
        except ValueError:
            logging.getLogger("django.request").error("Internal Server Error: %s", ruta, exc_info=True,
                                                      extra={"request": peticion, "status_code": 500})

    def test_la_pagina_muestra_disco_base_y_respaldo(self):
        (self.respaldos / "callflow_20261101_100000.dump").write_bytes(b"x" * 2048)
        with override_settings(CARPETA_RESPALDOS=self.respaldos):
            respuesta = self.client.get(reverse("ti:servidor"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Espacio en disco")
        self.assertContains(respuesta, "core_usuario")  # una de las tablas de la base
        base = respuesta.context["base"]
        self.assertGreater(base["tamano"], 0)
        self.assertGreaterEqual(base["conexiones"], 1)
        self.assertTrue(respuesta.context["discos"])
        self.assertTrue(respuesta.context["respaldo"]["existe"])
        self.assertEqual(respuesta.context["respaldo"]["archivo"], "callflow_20261101_100000.dump")

    def test_solo_ti_ve_el_estado_del_servidor(self):
        for usuario in (self.admin, self.superusuario, self.vendedor):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("ti:servidor")).status_code, 403, usuario.username)

    def test_la_pagina_se_muestra_aunque_la_base_no_responda(self):
        with mock.patch.object(servidor, "base_de_datos", side_effect=DatabaseError("conexión rechazada")):
            respuesta = self.client.get(reverse("ti:servidor"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "La base de datos no respondió")

    def test_niveles_de_disco_y_respaldo_atrasado(self):
        gb = 1024 ** 3
        self.assertEqual(servidor.Disco("C:\\", [], 100 * gb, 95 * gb, 5 * gb).nivel, "critico")
        self.assertEqual(servidor.Disco("C:\\", [], 100 * gb, 85 * gb, 15 * gb).nivel, "bajo")
        self.assertEqual(servidor.Disco("C:\\", [], 100 * gb, 50 * gb, 50 * gb).nivel, "ok")
        with override_settings(CARPETA_RESPALDOS=self.respaldos):
            self.assertFalse(servidor.ultimo_respaldo()["existe"])
            viejo = self.respaldos / "callflow_20261001_100000.dump"
            viejo.write_bytes(b"x")
            hace = time.time() - 5 * 86400
            os.utime(viejo, (hace, hace))
            self.assertTrue(servidor.ultimo_respaldo()["atrasado"])

    def test_los_errores_se_registran_y_se_agrupan(self):
        marca = uuid.uuid4().hex
        antes = registro_errores.contar_desde(24)
        for _ in range(3):
            self.registrar_error(f"falló {marca}", ruta="/ventas/bases/7/")
        self.registrar_error(f"otro {marca}", ruta="/ti/")
        self.assertEqual(registro_errores.contar_desde(24), antes + 4)
        propios = [e for e in registro_errores.errores_recientes(1) if marca in e["mensaje"] or marca in e.get("traza", "")]
        self.assertEqual(sorted(e["veces"] for e in propios), [1, 3])
        grupo = next(e for e in propios if e["veces"] == 3)
        self.assertEqual((grupo["excepcion"], grupo["metodo"], grupo["estado_http"]), ("ValueError", "GET", 500))
        self.assertEqual(grupo["usuarios"], {"lucia.mora"})
        self.assertIn("Traceback", grupo["traza"])
        pagina = self.client.get(reverse("ti:servidor"), {"dias": "1"})
        self.assertContains(pagina, marca)
        self.assertContains(pagina, "×3")

    def test_lineas_viejas_o_dañadas_se_ignoran(self):
        archivo = Path(settings.LOGS_DIR) / registro_errores.NOMBRE_ARCHIVO
        marca = uuid.uuid4().hex
        viejo = {"fecha": (timezone.localtime() - timedelta(days=40)).isoformat(), "nivel": "ERROR", "origen": "x",
                 "mensaje": f"viejo {marca}"}
        with archivo.open("a", encoding="utf-8") as f:
            f.write(json.dumps(viejo) + "\n")
            f.write("{esto no es json\n")
        mensajes = [e["mensaje"] for e in registro_errores.errores_recientes(30)]
        self.assertFalse(any(marca in m for m in mensajes))
