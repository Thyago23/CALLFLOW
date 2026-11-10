"""4.3 Módulo de ventas: SCRUM-38 a SCRUM-47."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.management import call_command
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cierre.models import GestionCierre, MetaMensual
from apps.core.models import Notificacion, Rol, Usuario
from apps.personal.models import Cargo, Empleado

from . import servicios, tarjetas
from .forms import sin_numero_de_tarjeta
from .models import AsignacionBase, BaseComercial, Cliente, FormularioDAMI, RegistroProduccion, SolicitudBase, TarjetaDAMI

CLAVE_PRUEBA = Fernet.generate_key().decode()
VISA = "4111 1111 1111 1111"

# Mismos campos que el DAMI en papel.
CLIENTE = {"cedula": "1710034065", "ruc": "", "nombres": "carla  andrea", "apellidos": "VEGA", "telefono": "0991234567",
           "direccion": "Av. Amazonas N34-12", "sector": "La Carolina", "ciudad": "quito", "referencia": "Junto al parque",
           "email": "Carla@Correo.com", "marca": "VISA", "banco": "Banco Pichincha", "caducidad": "08/35",
           "numero_tarjeta": VISA, "tarjeta_activa": "SI", "observaciones": ""}


@override_settings(CLAVE_TARJETAS=CLAVE_PRUEBA)
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
    """SCRUM-38 y SCRUM-41: DAMI con el formato en papel."""

    def test_el_vendedor_indica_si_la_tarjeta_esta_activa(self):
        """La tarjeta activa da prioridad 1 en cierre; antes se marcaba activa sola al guardar cualquier tarjeta."""
        self.crear(enviar=True)
        activa = FormularioDAMI.objects.get()
        self.assertEqual((activa.tiene_tarjeta_activa, activa.prioridad), (True, 1))
        self.crear(enviar=True, cedula="0926687856", tarjeta_activa="NO")
        inactiva = FormularioDAMI.objects.exclude(pk=activa.pk).get()
        self.assertEqual((inactiva.tiene_tarjeta_activa, inactiva.prioridad), (False, 2))
        # Con datos de tarjeta, la pregunta es obligatoria.
        sin_marcar = self.crear(cedula="1710000009", tarjeta_activa="")
        self.assertIn("tarjeta_activa", sin_marcar.context["form"].errors)
        self.assertContains(sin_marcar, "¿La tarjeta está activa?")
        self.assertNotContains(sin_marcar, 'name="telefono_alterno"')
        self.assertNotContains(sin_marcar, 'name="telefono3"')

    def test_guardar_borrador(self):
        respuesta = self.crear()
        f = FormularioDAMI.objects.get()
        self.assertEqual((f.estado, f.vendedor), ("BORRADOR", self.lucia.empleado))
        c = f.cliente
        self.assertEqual((c.cedula, c.nombres, c.apellidos, c.sector, c.ciudad, c.referencia, c.email),
                         ("1710034065", "Carla Andrea", "Vega", "La Carolina", "Quito", "Junto al parque", "carla@correo.com"))
        self.assertContains(respuesta, "guardado como borrador")
        self.assertFalse(RegistroProduccion.objects.exists())  # el borrador no va a la hoja

    def test_validaciones(self):
        form = self.crear(cedula="1710034066", telefono="abc").context["form"]
        self.assertEqual({"cedula", "telefono"}, set(form.errors))
        form = self.crear(cedula="", ruc="").context["form"]
        self.assertIn("Ingrese la cédula o el RUC", form.errors["cedula"][0])
        form = self.crear(ruc="17123").context["form"]
        self.assertIn("13 dígitos", form.errors["ruc"][0])
        self.assertFalse(FormularioDAMI.objects.exists())

    def test_solo_ruc(self):
        self.crear(cedula="", ruc="1790012345001")
        c = Cliente.objects.get()
        self.assertEqual((c.tipo_identificacion, c.identificacion, c.ruc_mostrar, c.cedula), ("RUC", "1790012345001", "1790012345001", ""))

    def test_enviar_notifica_a_encargados_y_crea_fila_en_la_hoja(self):
        respuesta = self.crear(enviar=True)
        f = FormularioDAMI.objects.get()
        self.assertEqual(f.estado, "ENVIADO")
        self.assertIsNotNone(f.enviado_en)
        self.assertRedirects(respuesta, reverse("ventas:hoja"))  # el vendedor termina en su hoja
        self.assertContains(respuesta, "enviado a los encargados de cierre")
        aviso = Notificacion.objects.get(destinatario=self.ana, tipo="FORMULARIO_DAMI")
        self.assertNotIn("1111", aviso.titulo + aviso.mensaje)  # sin datos del número de tarjeta
        self.assertFalse(Notificacion.objects.filter(destinatario=self.pedro).exists())
        registro = RegistroProduccion.objects.get(formulario=f)  # SCRUM-43
        self.assertEqual((registro.estado, registro.hoja.vendedor), ("PENDIENTE", self.lucia.empleado))
        historial = list(f.historial.values_list("estado_nuevo", "usuario_id"))
        self.assertEqual(historial, [("BORRADOR", self.lucia.pk), ("ENVIADO", self.lucia.pk)])

    def test_enviado_el_vendedor_ya_no_lo_abre(self):
        self.crear(enviar=True)
        enviado = FormularioDAMI.objects.get()
        for vista in ("ventas:formulario_detalle", "ventas:formulario_editar"):
            respuesta = self.client.get(reverse(vista, args=[enviado.pk]), follow=True)
            self.assertRedirects(respuesta, reverse("ventas:hoja"))
            self.assertContains(respuesta, "ya no se puede abrir")
        self.assertNotContains(self.client.get(reverse("ventas:formularios")), str(enviado))
        hoja = self.client.get(reverse("ventas:hoja"))
        self.assertContains(hoja, str(enviado))  # queda en la hoja de producción…
        for dato in ("1111", "Visa", "Pichincha", "08/35"):
            self.assertNotContains(hoja, dato)  # …sin información de la tarjeta
        self.assertNotContains(hoja, reverse("ventas:formulario_detalle", args=[enviado.pk]))

    def test_borrador_se_puede_anular(self):
        self.crear()
        borrador = FormularioDAMI.objects.get()
        self.client.post(reverse("ventas:formulario_accion", args=[borrador.pk]), {"accion": "anular"})
        borrador.refresh_from_db()
        self.assertEqual(borrador.estado, "ANULADO")

    def test_enviar_un_borrador_existente(self):
        self.crear()
        f = FormularioDAMI.objects.get()
        self.client.post(reverse("ventas:formulario_accion", args=[f.pk]), {"accion": "enviar"})
        f.refresh_from_db()
        self.assertEqual(f.estado, "ENVIADO")

    def test_no_se_envia_sin_tarjeta(self):
        sin_tarjeta = {"marca": "", "banco": "", "caducidad": "", "numero_tarjeta": ""}
        self.crear(**sin_tarjeta)
        f = FormularioDAMI.objects.get()
        respuesta = self.client.post(reverse("ventas:formulario_accion", args=[f.pk]), {"accion": "enviar"}, follow=True)
        self.assertContains(respuesta, "complete los datos de la tarjeta")
        f.refresh_from_db()
        self.assertEqual(f.estado, "BORRADOR")

    def test_cada_vendedor_ve_solo_lo_suyo(self):
        self.crear()
        f = FormularioDAMI.objects.get()
        self.client.force_login(self.pedro)
        self.assertEqual(self.client.get(reverse("ventas:formulario_detalle", args=[f.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("ventas:formulario_editar", args=[f.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("ventas:formularios")), str(f))
        # Encargados y administradores ven todos (solo lectura).
        for usuario in [self.ana, self.admin]:
            self.client.force_login(usuario)
            self.assertContains(self.client.get(reverse("ventas:formularios")), str(f))
            self.assertEqual(self.client.get(reverse("ventas:formulario_nuevo")).status_code, 403)


class ClientesTests(BaseVentas):
    """SCRUM-39"""

    def test_reutiliza_cliente_y_actualiza_contacto(self):
        self.crear()
        self.crear(usuario=self.pedro, telefono="0987654321", sector="Iñaquito")
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual((Cliente.objects.get().telefono, Cliente.objects.get().sector), ("0987654321", "Iñaquito"))
        self.assertEqual(FormularioDAMI.objects.count(), 2)

    def test_busqueda_en_vivo_llena_los_datos_y_avisa_si_esta_en_curso(self):
        self.crear(enviar=True)
        self.client.force_login(self.pedro)
        url = reverse("ventas:buscar_cliente")
        respuesta = self.client.get(url, {"cedula": "1710034065", "ruc": ""})
        self.assertContains(respuesta, "Cliente ya registrado")
        self.assertContains(respuesta, "en curso")
        self.assertIn("clienteEncontrado", respuesta["HX-Trigger"])
        self.assertIn("La Carolina", respuesta["HX-Trigger"])
        self.assertNotIn("1111", respuesta["HX-Trigger"])
        self.assertContains(self.client.get(url, {"cedula": "0926687856"}), "Cliente nuevo")
        self.assertContains(self.client.get(url, {"cedula": "1234567890"}), "no es válida")


class TarjetaTests(BaseVentas):
    """SCRUM-40: tarjeta para realizar el pago, cifrada."""

    def test_numero_cifrado_y_solo_ultimos_4_a_la_vista(self):
        self.crear(enviar=True)
        f = FormularioDAMI.objects.get()
        tarjeta = TarjetaDAMI.objects.get(formulario=f)
        self.assertEqual((tarjeta.marca, tarjeta.banco, tarjeta.caducidad, tarjeta.ultimos4), ("VISA", "Banco Pichincha", "08/35", "1111"))
        self.assertNotIn("4111111111111111", tarjeta.numero_cifrado)
        self.assertEqual(tarjetas.descifrar(tarjeta.numero_cifrado), "4111111111111111")
        self.assertEqual(f.prioridad, 1)
        # El número completo no aparece en la auditoría ni en las pantallas del encargado.
        from apps.core.models import RegistroCambio
        self.assertFalse(any("4111111111111111" in str(a.datos_nuevos) + str(a.datos_anteriores) for a in RegistroCambio.objects.all()))
        self.client.force_login(self.ana)
        detalle = self.client.get(reverse("ventas:formulario_detalle", args=[f.pk]))
        self.assertContains(detalle, "**** 1111")
        self.assertNotContains(detalle, "4111")

    def test_validaciones_de_la_tarjeta(self):
        form = self.crear(numero_tarjeta="4111 1111 1111 1112").context["form"]
        self.assertIn("no es válido", form.errors["numero_tarjeta"][0])
        form = self.crear(marca="AMEX").context["form"]
        self.assertIn("15", form.errors["numero_tarjeta"][0])
        form = self.crear(marca="MASTERCARD").context["form"]
        self.assertIn("no corresponde a una tarjeta Mastercard", form.errors["numero_tarjeta"][0])
        form = self.crear(caducidad="01/20", banco="", marca="").context["form"]
        self.assertEqual({"caducidad", "banco", "marca"}, set(form.errors))
        self.assertIn("caducada", form.errors["caducidad"][0])
        self.assertEqual(tarjetas.validar_caducidad("1031", date(2026, 11, 1)), (10, 2031))  # sin slash también vale
        self.assertFalse(FormularioDAMI.objects.exists())
        self.assertNotIn(b"4111", self.crear(numero_tarjeta="4111 1111 1111 1112").content)  # no se devuelve al navegador
        self.crear(marca="MASTERCARD", numero_tarjeta="5555 5555 5555 4444")
        self.assertEqual(TarjetaDAMI.objects.get().ultimos4, "4444")

    def test_borrador_conserva_la_tarjeta_sin_reescribir_el_numero(self):
        self.crear()
        f = FormularioDAMI.objects.get()
        antes = TarjetaDAMI.objects.get().numero_cifrado
        self.client.post(reverse("ventas:formulario_editar", args=[f.pk]), CLIENTE | {"numero_tarjeta": "", "banco": "Produbanco"})
        tarjeta = TarjetaDAMI.objects.get()
        self.assertEqual((tarjeta.banco, tarjeta.numero_cifrado), ("Produbanco", antes))
        form = self.client.post(reverse("ventas:formulario_editar", args=[f.pk]),
                                CLIENTE | {"numero_tarjeta": "", "marca": "AMEX"}).context["form"]
        self.assertIn("vuelva a escribir el número", form.errors["numero_tarjeta"][0])

    def test_no_se_aceptan_numeros_de_tarjeta_en_textos(self):
        with self.assertRaises(ValidationError):
            sin_numero_de_tarjeta("Pagará con 4111 1111 1111 1111")
        self.assertEqual(sin_numero_de_tarjeta("Llamar al 0991234567"), "Llamar al 0991234567")
        form = self.crear(observaciones="tarjeta 4111-1111-1111-1111").context["form"]
        self.assertIn("Tarjeta No", form.errors["observaciones"][0])
        self.assertFalse(FormularioDAMI.objects.exists())


class HojaProduccionTests(BaseVentas):
    """SCRUM-43 y SCRUM-44"""

    def test_la_hoja_se_actualiza_al_cerrar_y_suma_totales(self):
        self.crear(enviar=True)
        self.crear(enviar=True, cedula="0926687856")
        self.crear(enviar=True, cedula="1710000009")
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

    def test_columnas_como_la_hoja_en_papel(self):
        """N°, fecha, nombre cliente, ciudad y valor se llenan solos; no hay columna Completa/Cuota."""
        self.crear(enviar=True)
        self.crear(enviar=True, cedula="0926687856", nombres="luis", apellidos="paredes", ciudad="guayaquil")
        f1, f2 = FormularioDAMI.objects.order_by("pk")
        self.client.force_login(self.lucia)
        respuesta = self.client.get(reverse("ventas:hoja"))
        for texto in ("Nombre asesor/a", "Lucía Mora", "Carla Andrea Vega", "Luis Paredes", "Guayaquil", "Aprobación"):
            self.assertContains(respuesta, texto)
        self.assertNotContains(respuesta, "Completa/Cuota")
        self.assertEqual(len(respuesta.context["filas_vacias"]), 24)  # 26 renglones como en papel
        self.cerrar(f1, valor=D("320"))
        self.cerrar(f2, valor=D("99.90"))
        self.assertContains(self.client.get(reverse("ventas:hoja")), "419,90")

    def test_vendedor_escribe_la_aprobacion_como_hora(self):
        self.crear(enviar=True)
        registro = RegistroProduccion.objects.get()
        url = reverse("ventas:hoja_aprobacion", args=[registro.pk])
        self.assertContains(self.client.get(reverse("ventas:hoja")), 'type="time"')
        for invalido, mensaje in [("AB12", "HH:MM"), ("004512", "HH:MM"), ("25:10", "no existe"), ("09:75", "no existe")]:
            with self.subTest(invalido=invalido):
                self.assertContains(self.client.post(url, {"aprobacion": invalido}), mensaje)
                registro.refresh_from_db()
                self.assertEqual(registro.aprobacion, "")
        respuesta = self.client.post(url, {"aprobacion": "09:18"}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "aprobacion-fija")
        self.assertContains(respuesta, "09:18")
        registro.refresh_from_db()
        self.assertEqual(registro.aprobacion, "0918")
        self.assertContains(self.client.get(reverse("ventas:hoja")), "09:18")
        # otro vendedor no toca filas ajenas
        self.client.force_login(self.pedro)
        self.assertEqual(self.client.post(url, {"aprobacion": "1"}).status_code, 403)

    def test_la_aprobacion_guardada_queda_fija(self):
        self.crear(enviar=True)
        registro = RegistroProduccion.objects.get()
        url = reverse("ventas:hoja_aprobacion", args=[registro.pk])
        self.client.post(url, {"aprobacion": "09:18"})
        for otro in ("09:20", ""):
            with self.subTest(otro=otro):
                respuesta = self.client.post(url, {"aprobacion": otro})
                self.assertContains(respuesta, "no se puede modificar")
                self.assertNotContains(respuesta, 'name="aprobacion"')
                registro.refresh_from_db()
                self.assertEqual(registro.aprobacion, "0918")
        hoja = self.client.get(reverse("ventas:hoja"))
        self.assertContains(hoja, "09:18")
        self.assertNotContains(hoja, f'id="aprobacion-{registro.pk}"')
        self.assertContains(self.client.post(url, {"aprobacion": "09:18"}), "aprobacion-fija")  # repetir el mismo valor no es error

    def test_formatos_de_hora_aceptados(self):
        for texto, guardado in [("09:18", "0918"), ("9:18", "0918"), ("0918", "0918"), ("918", "0918"), ("23:59", "2359"), ("", "")]:
            with self.subTest(texto=texto):
                self.assertEqual(servicios.hora_aprobacion(texto), guardado)

    def test_la_base_rechaza_aprobacion_con_letras(self):
        from django.db import IntegrityError

        self.crear(enviar=True)
        with self.assertRaises(IntegrityError):
            RegistroProduccion.objects.update(aprobacion="12A")

    def test_anular_no_suma(self):
        self.crear()
        f = FormularioDAMI.objects.get()
        servicios.anular_formulario(self.lucia, f)
        hoy = timezone.localdate()
        self.assertEqual(servicios.hoja_del_mes(self.lucia.empleado, hoy.year, hoy.month)["totales"]["enviados"], 0)

    def test_solo_vendedores_tienen_hoja(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("ventas:hoja")).status_code, 403)


class AdministradorSinTarjetasTests(BaseVentas):
    """El administrador no entra a la validación de tarjetas, aunque sea superusuario; sí gestiona las bases comerciales."""

    def administradores(self):
        return (self.admin, Usuario.objects.create_superuser("root.callflow", password="x"))

    def test_no_entra_a_las_tarjetas(self):
        for usuario in self.administradores():
            self.assertEqual(usuario.rol, Rol.ADMINISTRADOR)
            self.client.force_login(usuario)
            for nombre in ("ventas:validar_tarjeta", "ventas:catalogo_bin"):
                self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, (usuario, nombre))
            self.assertEqual(self.client.post(reverse("ventas:validar_tarjeta"), {"numero": VISA}).status_code, 403)
            self.assertEqual(self.client.post(reverse("ventas:eliminar_bin", args=[1])).status_code, 403)

    def test_gestiona_las_bases_comerciales(self):
        solicitud = servicios.solicitar_base(self.lucia, "")
        for usuario in self.administradores():
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("ventas:bases")).status_code, 200)
            self.assertContains(self.client.get(reverse("ventas:formularios")), reverse("ventas:bases"))
        url = reverse("ventas:responder_solicitud", args=[solicitud.pk])
        self.client.post(url, {"accion": "rechazar", "respuesta": "No hay bases nuevas"})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, "RECHAZADA")

    def test_no_ve_el_enlace_de_tarjetas_y_conserva_las_hojas(self):
        for usuario in self.administradores():
            self.client.force_login(usuario)
            respuesta = self.client.get(reverse("ventas:formularios"))
            self.assertNotContains(respuesta, reverse("ventas:validar_tarjeta"))
            self.assertEqual(self.client.get(reverse("ventas:hojas_vendedores")).status_code, 200)

    def test_permisos_del_grupo_administrador(self):
        permisos = self.admin.get_group_permissions()
        self.assertFalse(permisos & {"ventas.view_rangobin", "ventas.add_rangobin"})
        self.assertTrue({"ventas.view_basecomercial", "ventas.add_basecomercial", "ventas.change_solicitudbase"} <= permisos)

    def test_los_demas_roles_siguen_entrando(self):
        for usuario, nombres in ((self.ana, ("ventas:validar_tarjeta", "ventas:bases")), (self.lucia, ("ventas:validar_tarjeta", "ventas:bases"))):
            self.client.force_login(usuario)
            for nombre in nombres:
                self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, (usuario, nombre))


class HojasDelAdministradorTests(BaseVentas):
    """El administrador consulta la hoja de producción de cada vendedor, sin modificarla."""

    def url_de(self, usuario):
        return reverse("ventas:hoja_vendedor", args=[usuario.empleado.pk])

    def test_listado_con_todos_los_vendedores_y_sus_totales(self):
        self.crear(enviar=True)
        self.cerrar(FormularioDAMI.objects.get(), valor=D("150.50"))
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("ventas:hojas_vendedores"))
        self.assertEqual([f["vendedor"] for f in respuesta.context["filas"]], [self.lucia.empleado, self.pedro.empleado])
        self.assertContains(respuesta, "150,50")
        self.assertContains(respuesta, self.url_de(self.pedro))
        self.assertNotContains(respuesta, "Ana Torres")

    def test_ve_la_hoja_individual_en_solo_lectura(self):
        self.crear(enviar=True)
        registro = RegistroProduccion.objects.get()
        self.assertEqual(self.client.post(reverse("ventas:hoja_aprobacion", args=[registro.pk]), {"aprobacion": "14:05"}).status_code, 200)
        self.crear(usuario=self.pedro, enviar=True, cedula="0926687856", nombres="luis", apellidos="paredes")
        self.client.force_login(self.admin)
        respuesta = self.client.get(self.url_de(self.lucia))
        for texto in ("Lucía Mora", "Carla Andrea Vega", "14:05"):
            self.assertContains(respuesta, texto)
        self.assertNotContains(respuesta, "Luis Paredes")  # solo la hoja del vendedor pedido
        self.assertNotContains(respuesta, "hx-post")
        self.assertNotContains(respuesta, 'name="aprobacion"')

    def test_el_administrador_no_escribe_la_aprobacion(self):
        self.crear(enviar=True)
        registro = RegistroProduccion.objects.get()
        self.client.force_login(self.admin)
        url = reverse("ventas:hoja_aprobacion", args=[registro.pk])
        self.assertEqual(self.client.post(url, {"aprobacion": "1"}).status_code, 403)
        registro.refresh_from_db()
        self.assertEqual(registro.aprobacion, "")

    def test_respeta_el_mes_pedido(self):
        self.crear(enviar=True)
        self.client.force_login(self.admin)
        pasado = self.client.get(self.url_de(self.lucia), {"mes": "2026-01"})
        self.assertEqual(pasado.context["mes_actual"], date(2026, 1, 1))
        self.assertEqual(pasado.context["registros"], [])
        self.assertContains(self.client.get(reverse("ventas:hojas_vendedores"), {"mes": "2026-01"}), "?mes=2026-01")

    def test_mes_invalido_o_futuro_muestra_el_mes_en_curso(self):
        self.client.force_login(self.admin)
        hoy = timezone.localdate()
        for mes in ("abc", "2026-13", "9999-12", "99999999999999999999-01"):
            respuesta = self.client.get(reverse("ventas:hojas_vendedores"), {"mes": mes})
            self.assertEqual(respuesta.context["mes_actual"], hoy.replace(day=1), mes)

    def test_vendedor_que_dejo_el_cargo_sigue_consultable_si_tiene_hoja(self):
        self.crear(enviar=True)
        Empleado.objects.filter(pk=self.lucia.empleado.pk).update(estado=Empleado.Estado.INACTIVO)
        Usuario.objects.filter(pk=self.lucia.pk).update(rol=Rol.ENCARGADO_CIERRE)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url_de(self.lucia)).status_code, 200)
        self.assertContains(self.client.get(reverse("ventas:hojas_vendedores")), "Lucía Mora")

    def test_solo_el_administrador_entra(self):
        urls = (reverse("ventas:hojas_vendedores"), self.url_de(self.lucia))
        for usuario in (self.lucia, self.pedro, self.ana):
            self.client.force_login(usuario)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403, (usuario, url))

    def test_quien_no_es_vendedor_no_tiene_hoja(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url_de(self.ana)).status_code, 404)

    def test_el_menu_lleva_al_listado_solo_al_administrador(self):
        enlace = reverse("ventas:hojas_vendedores")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("ventas:formularios")), enlace)
        self.client.force_login(self.lucia)
        self.assertNotContains(self.client.get(reverse("ventas:formularios")), enlace)


class PanelTests(BaseVentas):
    """SCRUM-45"""

    def test_panel_con_meta_y_tasa_de_cierre(self):
        hoy = timezone.localdate()
        MetaMensual.objects.create(vendedor=self.lucia.empleado, anio=hoy.year, mes=hoy.month, monto_meta=D("1000"))
        self.crear(enviar=True)
        self.crear(enviar=True, cedula="0926687856")
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
        self.assertFalse(Notificacion.objects.filter(destinatario=self.admin, tipo="SOLICITUD_BASE").exists())
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
        self.assertEqual(list(servicios.bases_del_vendedor(self.lucia.empleado)), [self.base])
        with self.assertRaisesMessage(ValidationError, "ya tiene asignada"):
            servicios.atender_solicitud(servicios.solicitar_base(self.lucia, ""), self.base, self.ana)

    def test_rechazar_exige_motivo(self):
        solicitud = servicios.solicitar_base(self.lucia, "")
        self.client.force_login(self.ana)
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


class AgendarLlamadaTests(BaseVentas):
    """Desde el DAMI se agenda la llamada al cliente; el recordatorio queda vinculado a ese cliente."""

    def test_boton_y_formulario_precargado(self):
        self.crear()
        f = FormularioDAMI.objects.get()
        url = reverse("agenda:crear") + f"?cliente={f.cliente.pk}"
        self.assertContains(self.client.get(reverse("ventas:formulario_detalle", args=[f.pk])), url)
        respuesta = self.client.get(url)
        form = respuesta.context["form"]
        self.assertEqual((form.initial["cliente"], form.initial["tipo"], form.initial["titulo"]),
                         (f.cliente.pk, "RECORDATORIO", "Llamar a Carla Andrea Vega"))
        self.assertContains(respuesta, "0991234567")

    def test_guardar_vincula_al_cliente_y_se_ve_en_la_agenda(self):
        from apps.agenda.models import Evento
        from apps.agenda.servicios import siguiente_laborable

        self.crear()
        f = FormularioDAMI.objects.get()
        dia = siguiente_laborable(timezone.localdate())
        self.client.post(reverse("agenda:crear"), {
            "titulo": "Llamar a Carla", "tipo": "RECORDATORIO", "fecha": dia.isoformat(), "hora_inicio": "10:00",
            "minutos_aviso": "5", "cliente": f.cliente.pk,
        })
        evento = Evento.objects.get()
        self.assertEqual((evento.cliente, evento.propietario), (f.cliente, self.lucia))
        dia_url = reverse("agenda:calendario") + f"?vista=dia&fecha={dia.isoformat()}"
        self.assertContains(self.client.get(dia_url), 'href="tel:0991234567"')
        self.assertContains(self.client.get(reverse("agenda:detalle", args=[evento.pk])), 'href="tel:0991234567"')

    def test_no_se_agenda_un_cliente_ajeno(self):
        self.crear(usuario=self.pedro)
        ajeno = FormularioDAMI.objects.get().cliente
        self.client.force_login(self.lucia)
        self.assertNotIn("cliente", self.client.get(reverse("agenda:crear") + f"?cliente={ajeno.pk}").context["form"].initial)
        respuesta = self.client.post(reverse("agenda:crear"), {
            "titulo": "Llamar", "tipo": "RECORDATORIO", "fecha": timezone.localdate().isoformat(), "hora_inicio": "10:00",
            "minutos_aviso": "5", "cliente": ajeno.pk,
        })
        self.assertIn("cliente", respuesta.context["form"].errors)


class MisEstadisticasTests(BaseVentas):
    """El vendedor ve su crecimiento: DAMI sacados, ventas y monto por mes frente a meses anteriores."""

    def setUp(self):
        from apps.reportes.estadisticas import sumar_meses

        self.hoy = timezone.localdate()
        a, m = sumar_meses(self.hoy.year, self.hoy.month, -1)
        self.mes_pasado = date(a, m, 1)  # el día 1: siempre cuenta como «a la misma fecha»

    def venta(self, cedula, valor=None, mes_pasado=False, usuario=None):
        self.crear(usuario or self.lucia, enviar=True, cedula=cedula)
        f = FormularioDAMI.objects.order_by("-pk").first()
        if valor:
            self.cerrar(f, valor=D(valor))
        if mes_pasado:
            RegistroProduccion.objects.filter(formulario=f).update(fecha=self.mes_pasado)
        return f

    def test_compara_con_el_mes_pasado_y_grafica(self):
        self.venta("1710034065", "300")
        self.venta("0926687856")  # DAMI sacado, aún sin cerrar
        self.venta("1710000009", "100", mes_pasado=True)
        self.venta("1714616123", "200", usuario=self.pedro)  # de otro vendedor: no cuenta
        self.client.force_login(self.lucia)
        respuesta = self.client.get(reverse("ventas:mis_estadisticas"))
        self.assertEqual(respuesta.status_code, 200)
        dami, ventas, monto = respuesta.context["comparaciones"]
        self.assertEqual((dami.actual, dami.misma_fecha, ventas.actual, ventas.misma_fecha), (2, 1, 1, 1))
        self.assertEqual((monto.actual, monto.misma_fecha, monto.variacion), (D("300"), D("100"), 200))
        self.assertTrue(monto.subio)
        self.assertEqual(len(respuesta.context["filas"]), 6)
        for texto in ("¡Vas creciendo!", "DAMI sacados", "Ventas cerradas", "<svg", "+200%"):
            self.assertContains(respuesta, texto)
        self.assertEqual(respuesta.context["mejor_mes"].total, D("300"))
        anual = self.client.get(reverse("ventas:mis_estadisticas"), {"meses": "12"})
        self.assertEqual(len(anual.context["filas"]), 12)

    def test_sin_datos_y_solo_para_vendedores(self):
        self.client.force_login(self.lucia)
        self.assertContains(self.client.get(reverse("ventas:mis_estadisticas")), "Todavía no hay datos para graficar")
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("ventas:mis_estadisticas")).status_code, 403)
