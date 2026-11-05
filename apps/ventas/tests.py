"""4.3 Módulo de ventas: SCRUM-38 a SCRUM-47."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cierre.models import GestionCierre, MetaMensual
from apps.core.models import Notificacion, Rol, Usuario
from apps.personal.models import Cargo, Empleado

from . import servicios
from .forms import sin_numero_de_tarjeta
from .models import AsignacionBase, BaseComercial, Cliente, FormularioDAMI, RegistroProduccion, SolicitudBase

CLIENTE = {"tipo_identificacion": "CEDULA", "identificacion": "1710034065", "nombres": "carla", "apellidos": "vega",
           "telefono": "0991234567", "telefono_alterno": "", "email": "Carla@Correo.com", "ciudad": "quito",
           "direccion": "Av. Amazonas", "producto": "Tarjeta de crédito Oro", "tiene_tarjeta_activa": "",
           "base_comercial": "", "observaciones": ""}


class BaseVentas(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.lucia = cls.persona("lucia.mora", "Lucía", "Mora", Rol.VENDEDOR, "1712345675", "Vendedor telefónico")
        cls.pedro = cls.persona("pedro.paz", "Pedro", "Paz", Rol.VENDEDOR, "0102030405", "Vendedor telefónico")
        cls.ana = cls.persona("ana.torres", "Ana", "Torres", Rol.ENCARGADO_CIERRE, "1804567893", "Encargado de cierre")
        cls.admin = Usuario.objects.create_user("diego.mendoza", None, "x", first_name="Diego", last_name="Mendoza", rol=Rol.ADMINISTRADOR)

    @classmethod
    def persona(cls, usuario, nombres, apellidos, rol, cedula, cargo):
        u = Usuario.objects.create_user(usuario, None, "x", first_name=nombres, last_name=apellidos, rol=rol)
        Empleado.objects.create(usuario=u, identificacion=cedula, nombres=nombres, apellidos=apellidos,
                                cargo=Cargo.objects.get(nombre=cargo), fecha_ingreso=date(2026, 1, 5), telefono="0990000000")
        return Usuario.objects.get(pk=u.pk)

    def crear(self, usuario=None, enviar=False, **cambios):
        self.client.force_login(usuario or self.lucia)
        datos = CLIENTE | cambios
        if enviar:
            datos["enviar"] = ""
        return self.client.post(reverse("ventas:formulario_nuevo"), datos, follow=True)

    def cerrar(self, formulario, resultado="VENTA_CERRADA", valor=D("150")):
        return GestionCierre.objects.create(formulario=formulario, encargado=self.ana.empleado, resultado=resultado,
                                            valor_venta=valor if resultado == "VENTA_CERRADA" else None)


class FormularioDAMITests(BaseVentas):
    """SCRUM-38 y SCRUM-41"""

    def test_guardar_borrador(self):
        respuesta = self.crear()
        f = FormularioDAMI.objects.get()
        self.assertEqual((f.estado, f.vendedor, f.producto), ("BORRADOR", self.lucia.empleado, "Tarjeta de crédito Oro"))
        self.assertEqual((f.cliente.nombres, f.cliente.ciudad, f.cliente.email), ("Carla", "Quito", "carla@correo.com"))
        self.assertContains(respuesta, "guardado como borrador")
        self.assertFalse(RegistroProduccion.objects.exists())  # el borrador no va a la hoja

    def test_validaciones(self):
        form = self.crear(identificacion="1710034066", telefono="abc", nombres="Carla123").context["form"]
        self.assertEqual({"identificacion", "telefono", "nombres"}, set(form.errors))
        form = self.crear(enviar=True, producto="").context["form"]
        self.assertIn("producto", form.errors)
        self.assertFalse(FormularioDAMI.objects.exists())

    def test_enviar_notifica_a_encargados_y_crea_fila_en_la_hoja(self):
        respuesta = self.crear(enviar=True)
        f = FormularioDAMI.objects.get()
        self.assertEqual(f.estado, "ENVIADO")
        self.assertIsNotNone(f.enviado_en)
        self.assertContains(respuesta, "enviado a los encargados de cierre")
        self.assertTrue(Notificacion.objects.filter(destinatario=self.ana, tipo="FORMULARIO_DAMI").exists())
        self.assertFalse(Notificacion.objects.filter(destinatario=self.pedro).exists())
        registro = RegistroProduccion.objects.get(formulario=f)  # SCRUM-43
        self.assertEqual((registro.estado, registro.hoja.vendedor), ("PENDIENTE", self.lucia.empleado))
        historial = list(f.historial.values_list("estado_nuevo", "usuario_id"))
        self.assertEqual(historial, [("BORRADOR", self.lucia.pk), ("ENVIADO", self.lucia.pk)])

    def test_enviado_ya_no_se_edita_y_borrador_se_puede_anular(self):
        self.crear(enviar=True)
        enviado = FormularioDAMI.objects.get()
        respuesta = self.client.get(reverse("ventas:formulario_editar", args=[enviado.pk]), follow=True)
        self.assertContains(respuesta, "no se puede modificar")
        self.crear(identificacion="0926687856")
        borrador = FormularioDAMI.objects.get(estado="BORRADOR")
        self.client.post(reverse("ventas:formulario_accion", args=[borrador.pk]), {"accion": "anular"})
        borrador.refresh_from_db()
        self.assertEqual(borrador.estado, "ANULADO")

    def test_enviar_un_borrador_existente(self):
        self.crear()
        f = FormularioDAMI.objects.get()
        self.client.post(reverse("ventas:formulario_accion", args=[f.pk]), {"accion": "enviar"})
        f.refresh_from_db()
        self.assertEqual(f.estado, "ENVIADO")

    def test_cada_vendedor_ve_solo_lo_suyo(self):
        self.crear()
        f = FormularioDAMI.objects.get()
        self.client.force_login(self.pedro)
        self.assertEqual(self.client.get(reverse("ventas:formulario_detalle", args=[f.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("ventas:formulario_editar", args=[f.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("ventas:formularios")), "Vega Carla")
        # Encargados y administradores ven todos (solo lectura).
        for usuario in [self.ana, self.admin]:
            self.client.force_login(usuario)
            self.assertContains(self.client.get(reverse("ventas:formularios")), "Vega Carla")
            self.assertEqual(self.client.get(reverse("ventas:formulario_nuevo")).status_code, 403)


class ClientesTests(BaseVentas):
    """SCRUM-39"""

    def test_reutiliza_cliente_y_actualiza_contacto(self):
        self.crear()
        self.crear(usuario=self.pedro, telefono="0987654321", producto="Seguro de vida")
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(Cliente.objects.get().telefono, "0987654321")
        self.assertEqual(FormularioDAMI.objects.count(), 2)

    def test_busqueda_en_vivo_llena_los_datos_y_avisa_si_esta_en_curso(self):
        self.crear(enviar=True)
        self.client.force_login(self.pedro)
        url = reverse("ventas:buscar_cliente")
        respuesta = self.client.get(url, {"tipo_identificacion": "CEDULA", "identificacion": "1710034065"})
        self.assertContains(respuesta, "Cliente ya registrado")
        self.assertContains(respuesta, "en curso")
        self.assertIn("clienteEncontrado", respuesta["HX-Trigger"])
        self.assertIn("Carla", respuesta["HX-Trigger"])
        self.assertContains(self.client.get(url, {"tipo_identificacion": "CEDULA", "identificacion": "0926687856"}), "Cliente nuevo")
        self.assertContains(self.client.get(url, {"tipo_identificacion": "CEDULA", "identificacion": "123"}), "no es válida")


class TarjetaActivaTests(BaseVentas):
    """SCRUM-40"""

    def test_tarjeta_activa_es_prioritaria(self):
        self.crear(enviar=True, tiene_tarjeta_activa="on")
        f = FormularioDAMI.objects.get()
        self.assertEqual(f.prioridad, 1)
        self.assertContains(self.client.get(reverse("ventas:formularios")), "Prioritario")
        self.assertIn("PRIORITARIO", Notificacion.objects.get(destinatario=self.ana).titulo)

    def test_no_se_aceptan_numeros_de_tarjeta(self):
        with self.assertRaises(ValidationError):
            sin_numero_de_tarjeta("Pagará con 4111 1111 1111 1111")
        self.assertEqual(sin_numero_de_tarjeta("Llamar al 0991234567"), "Llamar al 0991234567")
        form = self.crear(observaciones="tarjeta 4111-1111-1111-1111").context["form"]
        self.assertIn("No escriba números de tarjeta", form.errors["observaciones"][0])
        self.assertFalse(FormularioDAMI.objects.exists())


class HojaProduccionTests(BaseVentas):
    """SCRUM-43 y SCRUM-44"""

    def test_la_hoja_se_actualiza_al_cerrar_y_suma_totales(self):
        self.crear(enviar=True)
        self.crear(enviar=True, identificacion="0926687856", producto="Seguro")
        self.crear(enviar=True, identificacion="1710000009", producto="Crédito")
        f1, f2, f3 = FormularioDAMI.objects.order_by("pk")
        self.cerrar(f1, valor=D("150.50"))
        self.cerrar(f2, resultado="NO_CONCRETADA")
        hoy = timezone.localdate()
        datos = servicios.hoja_del_mes(self.lucia.empleado, hoy.year, hoy.month)
        t = datos["totales"]
        self.assertEqual((t["enviados"], t["cerradas"], t["no_concretadas"], t["pendientes"], t["total_vendido"], t["tasa_cierre"]),
                         (3, 1, 1, 1, D("150.50"), 50))
        self.client.force_login(self.lucia)
        respuesta = self.client.get(reverse("ventas:hoja"))
        self.assertContains(respuesta, "150,50")
        self.assertContains(respuesta, str(f3))

    def test_anular_no_suma(self):
        self.crear()
        f = FormularioDAMI.objects.get()
        servicios.anular_formulario(self.lucia, f)
        hoy = timezone.localdate()
        self.assertEqual(servicios.hoja_del_mes(self.lucia.empleado, hoy.year, hoy.month)["totales"]["enviados"], 0)

    def test_solo_vendedores_tienen_hoja(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("ventas:hoja")).status_code, 403)


class PanelTests(BaseVentas):
    """SCRUM-45"""

    def test_panel_con_meta_y_tasa_de_cierre(self):
        hoy = timezone.localdate()
        MetaMensual.objects.create(vendedor=self.lucia.empleado, anio=hoy.year, mes=hoy.month, monto_meta=D("1000"))
        self.crear(enviar=True)
        self.crear(enviar=True, identificacion="0926687856")
        f1, f2 = FormularioDAMI.objects.order_by("pk")
        self.cerrar(f1, valor=D("250"))
        self.cerrar(f2, resultado="NO_CONCRETADA")
        panel = servicios.panel_vendedor(self.lucia.empleado)
        self.assertEqual((panel.avance_meta, panel.pendiente_meta, panel.mes["tasa_cierre"]), (25, D("750"), 50))
        self.client.force_login(self.lucia)
        respuesta = self.client.get(reverse("ventas:inicio"))
        self.assertContains(respuesta, "25%")
        self.assertContains(respuesta, "750,00")

    def test_no_vendedor_va_a_la_lista(self):
        self.client.force_login(self.ana)
        self.assertRedirects(self.client.get(reverse("ventas:inicio")), reverse("ventas:formularios"))


class BasesComercialesTests(BaseVentas):
    """SCRUM-46 y SCRUM-47"""

    def setUp(self):
        self.base = BaseComercial.objects.create(nombre="Quito norte", total_registros=500)

    def test_solicitar_notifica_y_solo_una_pendiente(self):
        self.client.force_login(self.lucia)
        self.client.post(reverse("ventas:bases"), {"mensaje": "Terminé mi base"})
        self.assertEqual(SolicitudBase.objects.filter(vendedor=self.lucia.empleado).count(), 1)
        self.assertTrue(Notificacion.objects.filter(destinatario=self.ana, tipo="SOLICITUD_BASE").exists())
        self.assertTrue(Notificacion.objects.filter(destinatario=self.admin, tipo="SOLICITUD_BASE").exists())
        respuesta = self.client.post(reverse("ventas:bases"), {"mensaje": "otra"}, follow=True)
        self.assertContains(respuesta, "Ya tiene una solicitud pendiente")
        self.assertEqual(SolicitudBase.objects.count(), 1)

    def test_atender_asigna_la_base_y_avisa_al_vendedor(self):
        solicitud = servicios.solicitar_base(self.lucia, "")
        self.client.force_login(self.ana)
        self.client.post(reverse("ventas:responder_solicitud", args=[solicitud.pk]),
                         {"accion": "atender", "base": self.base.pk, "respuesta": "Suerte"})
        solicitud.refresh_from_db()
        self.assertEqual((solicitud.estado, solicitud.base_asignada, solicitud.atendida_por), ("ATENDIDA", self.base, self.ana))
        self.assertTrue(AsignacionBase.objects.filter(base=self.base, vendedor=self.lucia.empleado).exists())
        self.assertTrue(Notificacion.objects.filter(destinatario=self.lucia, titulo__contains="Quito norte").exists())
        # La base aparece para elegirla en el DAMI.
        self.assertEqual(list(servicios.bases_del_vendedor(self.lucia.empleado)), [self.base])
        with self.assertRaisesMessage(ValidationError, "ya tiene asignada"):
            servicios.atender_solicitud(servicios.solicitar_base(self.lucia, ""), self.base, self.ana)

    def test_rechazar_exige_motivo(self):
        solicitud = servicios.solicitar_base(self.lucia, "")
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("ventas:responder_solicitud", args=[solicitud.pk]), {"accion": "rechazar", "respuesta": ""}, follow=True)
        self.assertContains(respuesta, "Indique el motivo")
        self.client.post(reverse("ventas:responder_solicitud", args=[solicitud.pk]), {"accion": "rechazar", "respuesta": "No hay bases nuevas"})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, "RECHAZADA")
        self.assertTrue(Notificacion.objects.filter(destinatario=self.lucia, titulo__contains="rechazada").exists())

    def test_registrar_y_asignar_bases_desde_la_pantalla(self):
        self.client.force_login(self.ana)
        self.client.post(reverse("ventas:bases"), {"crear_base": "", "b-nombre": "Guayaquil sur", "b-descripcion": "",
                                                   "b-total_registros": "300", "b-activa": "on"})
        nueva = BaseComercial.objects.get(nombre="Guayaquil sur")
        respuesta = self.client.post(reverse("ventas:bases"), {"asignar": "", "a-base": nueva.pk,
                                                               "a-vendedores": [self.lucia.empleado.pk, self.pedro.empleado.pk]}, follow=True)
        self.assertContains(respuesta, "asignada a 2 vendedores")
        self.assertEqual(AsignacionBase.objects.filter(base=nueva).count(), 2)

    def test_vendedor_no_gestiona_bases(self):
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.post(reverse("ventas:bases"), {"crear_base": "", "b-nombre": "X", "b-total_registros": "1"}).status_code, 403)
        self.assertFalse(BaseComercial.objects.filter(nombre="X").exists())
        self.assertFalse(SolicitudBase.objects.exists())  # tampoco se toma como una solicitud
        solicitud = servicios.solicitar_base(self.pedro, "")
        self.assertEqual(self.client.post(reverse("ventas:responder_solicitud", args=[solicitud.pk]), {"accion": "rechazar", "respuesta": "x"}).status_code, 403)
