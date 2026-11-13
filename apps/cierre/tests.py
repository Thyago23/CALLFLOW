"""4.4 Módulo de cierre de ventas: SCRUM-48 a SCRUM-60."""
from datetime import date
from decimal import Decimal as D

from cryptography.fernet import Fernet
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, RegistroCambio, Rol, Usuario
from apps.ventas.models import FormularioDAMI, RegistroProduccion, SolicitudBase
from apps.ventas.servicios import solicitar_base, atender_solicitud, rechazar_solicitud
from apps.ventas.models import BaseComercial
from apps.ventas.tests import CLAVE_PRUEBA, CLIENTE, BaseVentas

from . import servicios
from .models import GestionCierre, MetaMensual, MovimientoAsignacion
from .templatetags.panel_cf import OCULTO

Estado = FormularioDAMI.Estado


@override_settings(CLAVE_TARJETAS=CLAVE_PRUEBA)
class BaseCierre(BaseVentas):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.luis = cls.persona("luis.vera", "Luis", "Vera", Rol.ENCARGADO_CIERRE, "0912345675", "Supervisor")

    def enviado(self, usuario=None, **cambios) -> FormularioDAMI:
        self.crear(usuario or self.lucia, enviar=True, **cambios)
        return FormularioDAMI.objects.order_by("-pk").first()

    def tomado(self, encargado=None, **cambios) -> FormularioDAMI:
        f = self.enviado(**cambios)
        return servicios.tomar(encargado or self.ana, f, f.version)

    def post(self, usuario, nombre, pk, datos):
        self.client.force_login(usuario)
        return self.client.post(reverse(nombre, args=[pk]), datos, follow=True)


class BandejaTests(BaseCierre):
    """SCRUM-48 y SCRUM-49."""

    def test_solo_encargados_y_administrador_entran(self):
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.get(reverse("cierre:bandeja")).status_code, 403)
        for usuario in (self.ana, self.admin):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("cierre:bandeja")).status_code, 200)

    def test_prioritarios_primero_y_luego_por_antiguedad(self):
        normal_viejo = self.enviado(cedula="1710034065")
        FormularioDAMI.objects.filter(pk=normal_viejo.pk).update(tiene_tarjeta_activa=False)
        prioritario = self.enviado(cedula="0926687856")
        self.assertEqual(list(servicios.bandeja()), [prioritario, normal_viejo])
        self.client.force_login(self.ana)
        respuesta = self.client.get(reverse("cierre:bandeja"))
        self.assertContains(respuesta, "Prioritario")
        self.assertContains(respuesta, "**** 1111")
        self.assertNotContains(respuesta, "4111 1111 1111 1111")
        solo = self.client.get(reverse("cierre:bandeja"), {"prioritarios": "1"}).context["formularios"]
        self.assertEqual(list(solo), [prioritario])
        self.assertEqual(servicios.resumen_bandeja()["prioritarios"], 1)

    def test_borradores_no_llegan_a_la_bandeja(self):
        self.crear()
        self.assertFalse(servicios.bandeja().exists())
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("cierre:formulario", args=[FormularioDAMI.objects.get().pk])).status_code, 404)

    def test_filtros_y_busqueda(self):
        f = self.enviado()
        self.client.force_login(self.ana)
        url = reverse("cierre:bandeja")
        self.assertEqual(list(self.client.get(url, {"q": "1710034065"}).context["formularios"]), [f])
        self.assertEqual(list(self.client.get(url, {"q": f"DAMI-{f.pk:06d}"}).context["formularios"]), [f])
        self.assertFalse(self.client.get(url, {"vendedor": self.pedro.empleado.pk}).context["formularios"])
        self.assertFalse(self.client.get(url, {"estado": "EN_GESTION"}).context["formularios"])
        manana = (timezone.localdate().toordinal() + 1)
        self.assertFalse(self.client.get(url, {"desde": date.fromordinal(manana).isoformat()}).context["formularios"])

    def test_aviso_al_llegar_un_formulario(self):
        f = self.enviado()
        aviso = Notificacion.objects.get(destinatario=self.ana)
        self.assertEqual(aviso.url, reverse("cierre:formulario", args=[f.pk]))
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(aviso.url), "Tomar para gestionar")


class AsignacionTests(BaseCierre):
    """SCRUM-50 y SCRUM-51: cola compartida, cada encargado toma su formulario."""

    def test_tomar_pone_en_gestion_y_deja_historial(self):
        f = self.enviado()
        respuesta = self.post(self.ana, "cierre:accion", f.pk, {"accion": "tomar", "version": f.version})
        f.refresh_from_db()
        self.assertEqual((f.estado, f.encargado_asignado), (Estado.EN_GESTION, self.ana.empleado))
        self.assertIsNotNone(f.asignado_en)
        mov = MovimientoAsignacion.objects.get()
        self.assertEqual((mov.accion, mov.a_encargado, mov.usuario), ("TOMAR", self.ana.empleado, self.ana))
        self.assertContains(respuesta, "Lo tomó Torres Ana")
        self.assertTrue(f.historial.filter(estado_nuevo="EN_GESTION", usuario_id=self.ana.pk).exists())
        # El aviso deja de estar pendiente también para el otro encargado.
        self.assertFalse(Notificacion.objects.filter(destinatario=self.luis, leida=False).exists())

    def test_solo_el_asignado_registra_el_resultado(self):
        f = self.tomado(self.ana)
        with self.assertRaises(PermissionDenied):
            servicios.registrar_resultado(self.luis, f, f.version, resultado="VENTA_CERRADA", valor_venta=D("10"))
        self.client.force_login(self.luis)
        self.assertContains(self.client.get(reverse("cierre:formulario", args=[f.pk])), "Solo esa persona puede registrar")

    def test_devolver_a_la_cola(self):
        f = self.tomado(self.ana)
        self.post(self.ana, "cierre:accion", f.pk, {"accion": "liberar", "version": f.version})
        f.refresh_from_db()
        self.assertEqual((f.estado, f.encargado_asignado), (Estado.ENVIADO, None))
        self.assertTrue(Notificacion.objects.filter(destinatario=self.luis, titulo__contains="volvió a la cola").exists())
        with self.assertRaises(PermissionDenied):
            g = self.tomado(self.ana)
            servicios.liberar(self.luis, g, g.version)

    def test_administrador_reasigna(self):
        f = self.tomado(self.ana)
        self.post(self.admin, "cierre:accion", f.pk, {"accion": "reasignar", "version": f.version, "encargado": self.luis.empleado.pk})
        f.refresh_from_db()
        self.assertEqual(f.encargado_asignado, self.luis.empleado)
        mov = MovimientoAsignacion.objects.get(accion="REASIGNAR")
        self.assertEqual((mov.de_encargado, mov.a_encargado, mov.usuario), (self.ana.empleado, self.luis.empleado, self.admin))
        self.assertTrue(Notificacion.objects.filter(destinatario=self.luis, titulo__contains="Se le asignó").exists())
        with self.assertRaises(PermissionDenied):
            servicios.reasignar(self.ana, f, f.version, self.ana.empleado)

    def test_vendedor_no_toma_ni_ve_la_bandeja(self):
        f = self.enviado()
        respuesta = self.post(self.lucia, "cierre:accion", f.pk, {"accion": "tomar", "version": f.version})
        self.assertEqual(respuesta.status_code, 403)


class NumeroDeTarjetaTests(BaseCierre):
    """El número completo solo lo ve quien cierra la venta, mientras la gestiona."""

    NUMERO = "4111 1111 1111 1111"
    MASCARA = "**** **** **** 1111"

    def ver(self, usuario, formulario):
        self.client.force_login(usuario)
        return self.client.get(reverse("cierre:formulario", args=[formulario.pk]))

    def test_el_encargado_a_cargo_lo_ve_completo_y_sin_cache(self):
        respuesta = self.ver(self.ana, self.tomado(self.ana))
        self.assertContains(respuesta, self.NUMERO)
        self.assertNotContains(respuesta, self.MASCARA)
        self.assertIn("no-store", respuesta["Cache-Control"])

    def test_nadie_mas_lo_ve(self):
        f = self.tomado(self.ana)
        for usuario in (self.luis, self.admin):
            respuesta = self.ver(usuario, f)
            self.assertNotContains(respuesta, self.NUMERO)
            self.assertContains(respuesta, self.MASCARA)

    def test_sin_tomarlo_no_se_ve(self):
        respuesta = self.ver(self.ana, self.enviado())
        self.assertNotContains(respuesta, self.NUMERO)
        self.assertContains(respuesta, self.MASCARA)

    def test_al_devolverlo_a_la_cola_deja_de_verse(self):
        f = self.tomado(self.ana)
        f = servicios.liberar(self.ana, f, f.version)
        self.assertNotContains(self.ver(self.ana, f), self.NUMERO)

    def test_con_el_resultado_registrado_deja_de_verse(self):
        f = self.tomado(self.ana)
        servicios.registrar_resultado(self.ana, f, f.version, resultado="VENTA_CERRADA", valor_venta=D("100"))
        f.refresh_from_db()
        respuesta = self.ver(self.ana, f)
        self.assertNotContains(respuesta, self.NUMERO)
        self.assertContains(respuesta, self.MASCARA)

    def test_la_bandeja_sigue_enmascarada(self):
        self.tomado(self.ana)
        self.client.force_login(self.ana)
        self.assertNotContains(self.client.get(reverse("cierre:bandeja")), self.NUMERO)

    def test_si_la_clave_cambio_muestra_la_mascara_sin_romper_la_pagina(self):
        f = self.tomado(self.ana)
        with override_settings(CLAVE_TARJETAS=Fernet.generate_key().decode()), self.assertLogs("apps.cierre.servicios", "ERROR"):
            respuesta = self.ver(self.ana, f)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, self.MASCARA)


class ResultadoTests(BaseCierre):
    """SCRUM-52 y SCRUM-53."""

    def test_venta_cerrada_actualiza_dami_y_hoja(self):
        f = self.tomado()
        respuesta = self.post(self.ana, "cierre:resultado", f.pk,
                              {"resultado": "VENTA_CERRADA", "valor_venta": "250.50", "version": f.version})
        self.assertContains(respuesta, "Se actualizó la hoja de producción")
        f.refresh_from_db()
        registro = RegistroProduccion.objects.get(formulario=f)
        self.assertEqual(f.estado, Estado.VENTA_CERRADA)
        self.assertEqual((registro.estado, registro.valor_venta), ("VENTA_CERRADA", D("250.50")))
        self.assertEqual(GestionCierre.objects.get().encargado, self.ana.empleado)
        self.assertTrue(Notificacion.objects.filter(destinatario=self.lucia, titulo__contains="venta cerrada").exists())
        # El vendedor lo ve en su hoja de producción.
        self.client.force_login(self.lucia)
        self.assertContains(self.client.get(reverse("ventas:hoja")), "250,50")

    def test_no_concretada_pide_motivo_y_no_valor(self):
        f = self.tomado()
        respuesta = self.post(self.ana, "cierre:resultado", f.pk, {"resultado": "NO_CONCRETADA", "version": f.version})
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("motivo", respuesta.context["form_resultado"].errors)
        self.post(self.ana, "cierre:resultado", f.pk, {"resultado": "NO_CONCRETADA", "valor_venta": "99",
                                                     "motivo": servicios.MOTIVOS_NO_CONCRETADA[0], "version": f.version})
        gestion = GestionCierre.objects.get()
        self.assertIsNone(gestion.valor_venta)
        self.assertEqual(RegistroProduccion.objects.get(formulario=f).estado, "NO_CONCRETADA")

    def test_otro_motivo_exige_escribirlo(self):
        f = self.tomado()
        datos = {"resultado": "NO_CONCRETADA", "motivo": "Otro motivo", "version": f.version}
        respuesta = self.post(self.ana, "cierre:resultado", f.pk, datos)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("motivo_otro", respuesta.context["form_resultado"].errors)
        self.post(self.ana, "cierre:resultado", f.pk, {**datos, "motivo_otro": "  Viaja fuera del país  "})
        self.assertEqual(GestionCierre.objects.get().motivo_no_concretada, "Otro motivo: Viaja fuera del país")

    def test_venta_cerrada_exige_valor(self):
        f = self.tomado()
        respuesta = self.post(self.ana, "cierre:resultado", f.pk, {"resultado": "VENTA_CERRADA", "valor_venta": "0", "version": f.version})
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(GestionCierre.objects.exists())

    def test_hay_que_tomarlo_antes(self):
        f = self.enviado()
        with self.assertRaises(ValidationError):
            servicios.registrar_resultado(self.ana, f, f.version, resultado="VENTA_CERRADA", valor_venta=D("10"))

    def test_sin_numeros_de_tarjeta_en_observaciones(self):
        f = self.tomado()
        respuesta = self.post(self.ana, "cierre:resultado", f.pk, {"resultado": "VENTA_CERRADA", "valor_venta": "10",
                                                                 "observaciones": "pagó con 4111 1111 1111 1111", "version": f.version})
        self.assertEqual(respuesta.status_code, 400)

    def test_solo_el_administrador_corrige_y_queda_auditado(self):
        f = self.tomado()
        gestion = servicios.registrar_resultado(self.ana, f, f.version, resultado="VENTA_CERRADA", valor_venta=D("100"))
        with self.assertRaises(PermissionDenied):
            servicios.corregir_resultado(self.ana, gestion, resultado="NO_CONCRETADA", valor_venta=None,
                                         motivo="Otro motivo", motivo_correccion="x")
        self.post(self.admin, "cierre:corregir", f.pk, {"resultado": "VENTA_CERRADA", "valor_venta": "180",
                                                      "motivo_correccion": "El cliente subió el plan"})
        gestion.refresh_from_db()
        self.assertEqual(gestion.valor_venta, D("180"))
        self.assertIn("El cliente subió el plan", gestion.observaciones)
        self.assertEqual(RegistroProduccion.objects.get(formulario=f).valor_venta, D("180"))
        cambio = RegistroCambio.objects.filter(tabla="cierre_gestioncierre", accion="UPDATE").latest("fecha")
        self.assertEqual(D(str(cambio.datos_anteriores["valor_venta"])), D("100"))


class ConcurrenciaTests(BaseCierre):
    """SCRUM-59: dos encargados sobre el mismo formulario."""

    def test_dos_encargados_toman_a_la_vez(self):
        f = self.enviado()
        version_en_pantalla = f.version
        servicios.tomar(self.ana, f, version_en_pantalla)
        with self.assertRaises(servicios.Conflicto):
            servicios.tomar(self.luis, f, version_en_pantalla)  # Luis aún veía el formulario sin asignar
        f.refresh_from_db()
        self.assertEqual(f.encargado_asignado, self.ana.empleado)

    def test_pantalla_desactualizada_no_sobrescribe(self):
        f = self.tomado()
        vieja = f.version
        servicios.liberar(self.ana, f, vieja)
        servicios.tomar(self.luis, FormularioDAMI.objects.get(pk=f.pk), FormularioDAMI.objects.get(pk=f.pk).version)
        respuesta = self.post(self.ana, "cierre:resultado", f.pk, {"resultado": "VENTA_CERRADA", "valor_venta": "50", "version": vieja})
        self.assertContains(respuesta, "Otra persona modificó este formulario")
        self.assertFalse(GestionCierre.objects.exists())

    def test_no_hay_dos_resultados(self):
        f = self.tomado()
        servicios.registrar_resultado(self.ana, f, f.version, resultado="VENTA_CERRADA", valor_venta=D("10"))
        f.refresh_from_db()
        with self.assertRaises(ValidationError):
            servicios.registrar_resultado(self.ana, f, f.version, resultado="NO_CONCRETADA", motivo="Otro motivo")
        self.assertEqual(GestionCierre.objects.count(), 1)


class MetasTests(BaseCierre):
    """SCRUM-55."""

    def test_definir_y_cambiar_metas(self):
        self.client.force_login(self.ana)
        url = reverse("cierre:metas")
        self.client.post(url, {"anio": 2026, "mes": 11, f"meta-{self.lucia.empleado.pk}": "1500", f"meta-{self.pedro.empleado.pk}": ""})
        meta = MetaMensual.objects.get()
        self.assertEqual((meta.vendedor, meta.monto_meta, meta.definida_por), (self.lucia.empleado, D("1500"), self.ana))
        self.client.post(url, {"anio": 2026, "mes": 11, f"meta-{self.lucia.empleado.pk}": "1800"})
        meta.refresh_from_db()
        self.assertEqual(meta.monto_meta, D("1800"))
        self.assertTrue(RegistroCambio.objects.filter(tabla="cierre_metamensual", accion="UPDATE").exists())
        respuesta = self.client.post(url, {"anio": 2026, "mes": 11, f"meta-{self.lucia.empleado.pk}": "-5"})
        self.assertContains(respuesta, "Ingrese un valor mayor a cero")

    def test_copiar_mes_anterior(self):
        MetaMensual.objects.create(vendedor=self.lucia.empleado, anio=2026, mes=10, monto_meta=D("900"))
        MetaMensual.objects.create(vendedor=self.pedro.empleado, anio=2026, mes=11, monto_meta=D("700"))
        MetaMensual.objects.create(vendedor=self.pedro.empleado, anio=2026, mes=10, monto_meta=D("500"))
        self.assertEqual(servicios.copiar_metas_mes_anterior(self.ana, 2026, 11), 1)
        self.assertEqual(MetaMensual.objects.get(vendedor=self.lucia.empleado, mes=11).monto_meta, D("900"))
        self.assertEqual(MetaMensual.objects.get(vendedor=self.pedro.empleado, mes=11).monto_meta, D("700"))  # no se pisa
        self.assertEqual(servicios.copiar_metas_mes_anterior(self.ana, 2026, 1), 0)  # enero copia de diciembre

    def test_solo_acepta_metas_de_vendedores_activos(self):
        """Antes guardaba cualquier «meta-<id>»: un encargado salía en el panel y en la TV, y un id inexistente daba error 500."""
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("cierre:metas"), {
            "anio": 2026, "mes": 11, f"meta-{self.lucia.empleado.pk}": "1500", f"meta-{self.ana.empleado.pk}": "900", "meta-999999": "100"})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(list(MetaMensual.objects.values_list("vendedor_id", flat=True)), [self.lucia.empleado.pk])
        self.assertNotIn(self.ana.empleado, [f.vendedor for f in servicios.panel_comercial(2026, 11, self.admin).filas])

    def test_vendedor_no_define_metas(self):
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.get(reverse("cierre:metas")).status_code, 403)


class PanelTests(BaseCierre):
    """SCRUM-54, 56, 57 y 58."""

    def cerrar_venta(self, usuario, valor, cedula):
        f = self.enviado(usuario, cedula=cedula)
        f = servicios.tomar(self.ana, f, f.version)
        servicios.registrar_resultado(self.ana, f, f.version, resultado="VENTA_CERRADA", valor_venta=D(valor))

    def setUp(self):
        hoy = timezone.localdate()
        self.anio, self.mes = hoy.year, hoy.month

    def test_totales_ranking_y_mejor_vendedor(self):
        self.cerrar_venta(self.lucia, "300", "1710034065")
        self.cerrar_venta(self.pedro, "500", "0926687856")
        pendiente = self.enviado(self.lucia, cedula="1103000004")
        anulado = self.crear(self.lucia, cedula="0912345675")  # borrador que se anula: no cuenta
        borrador = FormularioDAMI.objects.get(estado="BORRADOR")
        borrador.estado = "ANULADO"
        borrador.save()
        MetaMensual.objects.create(vendedor=self.lucia.empleado, anio=self.anio, mes=self.mes, monto_meta=D("1000"))

        panel = servicios.panel_comercial(self.anio, self.mes, self.ana)
        lucia = next(f for f in panel.filas if f.vendedor == self.lucia.empleado)
        self.assertEqual((lucia.total_formularios, lucia.ventas_cerradas, lucia.pendientes), (2, 1, 1))
        self.assertEqual((lucia.total_vendido, lucia.monto_meta, lucia.pendiente_meta), (D("300"), D("1000"), D("700")))
        self.assertEqual(panel.mejor.vendedor, self.pedro.empleado)
        self.assertEqual([f.posicion for f in panel.filas][:2], [1, 2])
        self.assertEqual(panel.totales["total_vendido"], D("800"))
        self.assertEqual(panel.totales["pendiente_meta"], D("700"))
        # Coincide con la hoja de producción del vendedor.
        from apps.ventas.servicios import hoja_del_mes
        self.assertEqual(hoja_del_mes(self.lucia.empleado, self.anio, self.mes)["totales"]["total_vendido"], lucia.total_vendido)
        self.assertIsNotNone(pendiente)

    def test_sin_ventas_no_hay_mejor_vendedor(self):
        self.enviado()
        panel = servicios.panel_comercial(self.anio, self.mes, self.ana)
        self.assertIsNone(panel.mejor)
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse("cierre:panel")), "Todavía no hay ventas cerradas este mes")

    def test_empate_por_valor_desempata_por_ventas(self):
        self.cerrar_venta(self.lucia, "200", "1710034065")
        self.cerrar_venta(self.pedro, "100", "0926687856")
        self.cerrar_venta(self.pedro, "100", "1103000004")
        self.assertEqual(servicios.panel_comercial(self.anio, self.mes, self.ana).mejor.vendedor, self.pedro.empleado)

    def test_ocultar_valores_no_los_envia_al_navegador(self):
        self.cerrar_venta(self.lucia, "4321.99", "1710034065")
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse("cierre:panel")), "4.321,99")
        self.client.post(reverse("cierre:ocultar_valores"), {"ocultar": "1"})
        respuesta = self.client.get(reverse("cierre:panel"))
        self.assertNotContains(respuesta, "4.321,99")
        self.assertContains(respuesta, OCULTO)
        self.assertContains(respuesta, "Ventas cerradas")  # las cantidades siguen visibles
        # No afecta a los demás.
        self.client.force_login(self.luis)
        self.assertContains(self.client.get(reverse("cierre:panel")), "4.321,99")


class SolicitudBaseNotificacionTests(BaseCierre):
    """SCRUM-60."""

    def test_avisa_a_encargados_con_enlace_directo(self):
        solicitud = solicitar_base(self.lucia, "Se me acabaron los contactos")
        avisos = Notificacion.objects.filter(tipo="SOLICITUD_BASE")
        self.assertEqual({a.destinatario for a in avisos}, {self.ana, self.luis})
        self.assertTrue(all(a.url.endswith(f"#solicitud-{solicitud.pk}") for a in avisos))
        self.assertTrue(all("Se me acabaron" in a.mensaje for a in avisos))

    def test_al_atenderla_desaparece_para_todos(self):
        base = BaseComercial.objects.create(nombre="Quito norte", total_registros=100)
        solicitud = solicitar_base(self.lucia, "")
        atender_solicitud(solicitud, base, self.ana)
        self.assertFalse(Notificacion.objects.filter(tipo="SOLICITUD_BASE", destinatario__in=[self.ana, self.luis],
                                                     leida=False).exists())
        self.assertTrue(Notificacion.objects.filter(destinatario=self.lucia, leida=False).exists())  # la respuesta al vendedor

    def test_al_rechazarla_tambien(self):
        solicitud = solicitar_base(self.pedro, "")
        rechazar_solicitud(solicitud, self.luis, "No hay bases nuevas")
        self.assertFalse(Notificacion.objects.filter(tipo="SOLICITUD_BASE", destinatario=self.ana, leida=False).exists())

    def test_contador_se_refresca_sin_recargar(self):
        self.client.force_login(self.ana)
        url = reverse("contadores")
        self.assertNotContains(self.client.get(url), 'id="pendientes-bases-menu" hx-swap-oob="true" class="badge rounded-pill text-bg-danger ms-auto "')
        solicitar_base(self.lucia, "")
        respuesta = self.client.get(url)
        self.assertContains(respuesta, 'title="Solicitudes de bases pendientes">1</span>')
        self.assertContains(respuesta, "bg-danger contador")
        pagina = self.client.get(reverse("inicio"))
        self.assertContains(pagina, 'hx-trigger="load, every 5s"')  # al abrir la página y cada 5 s
        self.assertContains(pagina, "Solicitudes de bases pendientes")
        self.assertEqual(SolicitudBase.objects.count(), 1)

    def test_vendedor_no_ve_contadores_de_encargado(self):
        self.client.force_login(self.lucia)
        self.assertNotContains(self.client.get(reverse("contadores")), "pendientes-bases")


class EncargadoSinFichaTests(TestCase):
    """Un encargado sin ficha de empleado ve por qué no puede gestionar y cómo se arregla."""

    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.marlon = Usuario.objects.create_user("marlon.prue", None, "x", first_name="Marlon", last_name="Prue",
                                                 rol=Rol.ENCARGADO_CIERRE)

    def test_bandeja_explica_que_falta_la_ficha(self):
        self.client.force_login(self.marlon)
        respuesta = self.client.get(reverse("cierre:bandeja"))
        self.assertContains(respuesta, "no está vinculada a una ficha de empleado")
        self.assertContains(respuesta, "marlon.prue")


class TableroVentasTests(BaseCierre):
    """Tablero de ventas para la TV del call: todos los vendedores con lo vendido en el mes."""

    def cerrar_venta(self, usuario, valor, cedula):
        f = self.enviado(usuario, cedula=cedula)
        f = servicios.tomar(self.ana, f, f.version)
        servicios.registrar_resultado(self.ana, f, f.version, resultado="VENTA_CERRADA", valor_venta=D(valor))

    def test_muestra_a_todos_y_destaca_al_mejor(self):
        self.cerrar_venta(self.lucia, "300", "1710034065")
        self.cerrar_venta(self.lucia, "250", "1103000004")
        self.cerrar_venta(self.pedro, "450.50", "0926687856")
        self.client.force_login(self.ana)
        respuesta = self.client.get(reverse("tablero:ventas"))
        self.assertEqual(respuesta.status_code, 200)
        for texto in ("Lucía Mora", "Pedro Paz", "$550", "$450", "$1.000", "¡Va a la cabeza!", "3 ventas cerradas"):
            self.assertContains(respuesta, texto)
        self.assertEqual(respuesta.context["mejor"].vendedor, self.lucia.empleado)
        self.assertEqual([f.ancho for f in respuesta.context["filas"]], [100, 82])

    def test_consultas_constantes_aunque_crezcan_los_vendedores_y_los_gestionados(self):
        """Antes el tablero pedía el usuario de cada vendedor y «Gestionados» la tarjeta de cada fila (una consulta por fila)."""
        self.cerrar_venta(self.lucia, "300", "1710034065")
        self.client.force_login(self.ana)
        url_tv, url_g = reverse("tablero:ventas"), reverse("cierre:bandeja")

        def consultas(url, datos=None):
            # La primera petición hace trabajo de una sola vez (avisos push pendientes, cachés): se descarta.
            self.client.get(url, datos or {}, HTTP_HX_REQUEST="true")
            with CaptureQueriesContext(connection) as c:
                respuesta = self.client.get(url, datos or {}, HTTP_HX_REQUEST="true")
            self.assertEqual(respuesta.status_code, 200)
            return len(c)

        antes_tv, antes_g = consultas(url_tv), consultas(url_g, {"vista": "gestionados"})
        for i, cedula in enumerate(["0926687856", "1103000004", "1710000009"]):
            nuevo = self.persona(f"extra.{i}", f"Extra{i}", "Prueba", Rol.VENDEDOR, ["1710000009", "0912345683", "1712345683"][i], "Asesor de ventas")
            self.cerrar_venta(nuevo, "100", cedula)
        self.client.force_login(self.ana)  # cerrar_venta inicia sesión como el vendedor
        self.assertEqual(consultas(url_tv), antes_tv)
        self.assertEqual(consultas(url_g, {"vista": "gestionados"}), antes_g)

    def test_venta_mas_alta_y_sin_destacar(self):
        self.cerrar_venta(self.lucia, "300", "1710034065")
        self.cerrar_venta(self.lucia, "250", "1103000004")
        self.cerrar_venta(self.pedro, "450.50", "0926687856")
        self.client.force_login(self.admin)
        venta = self.client.get(reverse("tablero:ventas"), {"destacar": "venta"})
        self.assertContains(venta, "La venta más alta del mes")
        self.assertEqual((venta.context["venta_mas_alta"].valor_venta, venta.context["venta_nombre"]), (D("450.50"), "Pedro Paz"))
        nada = self.client.get(reverse("tablero:ventas"), {"destacar": "nada"})
        self.assertNotContains(nada, "¡Va a la cabeza!")
        self.assertNotContains(nada, "La venta más alta del mes")

    def test_mes_sin_ventas_y_actualizacion_htmx(self):
        self.client.force_login(self.ana)
        vacio = self.client.get(reverse("tablero:ventas"))
        self.assertContains(vacio, "¡Vamos por la primera!")
        self.assertIsNone(vacio.context["mejor"])
        parcial = self.client.get(reverse("tablero:ventas"), HTTP_HX_REQUEST="true")
        self.assertContains(parcial, "data-tablero")
        self.assertNotContains(parcial, "<html")

    def test_solo_administrador_y_cierre(self):
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.get(reverse("tablero:ventas")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("inicio")), "Tablero de ventas")
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse("inicio")), reverse("tablero:ventas"))
