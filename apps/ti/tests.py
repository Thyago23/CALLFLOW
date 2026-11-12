"""Módulo de TI: cuentas fijas, acceso exclusivo, inventario de equipos, conexiones y reportes."""
from datetime import timedelta
from io import StringIO

from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.utils import InternalError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.asistencia.models import RegistroAsistencia
from apps.core.models import CUENTAS_TI, Rol, Usuario
from apps.personal.models import Cargo, Empleado

from . import servicios
from .models import Equipo, EventoEquipo, SesionEquipo

CLAVE = "Clave-Segura-2026"


class BaseTI(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.santiago = Usuario.objects.get(username="santiago.cedeno")
        cls.nicolas = Usuario.objects.get(username="nicolas.correa")
        cls.admin = Usuario.objects.create_user("diego.mendoza", password=CLAVE, first_name="Diego", last_name="Mendoza",
                                                rol=Rol.ADMINISTRADOR)
        cls.superusuario = Usuario.objects.create_superuser("administrador.callflow", password=CLAVE)
        cls.vendedor = Usuario.objects.create_user("lucia.mora", password=CLAVE, first_name="Lucía", last_name="Mora",
                                                   rol=Rol.VENDEDOR)
        cargo = Cargo.objects.get(nombre="Vendedor telefónico")
        cls.empleado = Empleado.objects.create(usuario=cls.vendedor, tipo_identificacion="CEDULA", identificacion="1710034065",
                                               nombres="Lucía", apellidos="Mora", cargo=cargo, fecha_ingreso=timezone.localdate())

    def setUp(self):
        self.client.force_login(self.santiago)


class CuentasTITests(BaseTI):
    def test_las_dos_cuentas_existen_con_rol_ti_y_protegidas(self):
        self.assertEqual([c[0] for c in CUENTAS_TI], ["santiago.cedeno", "nicolas.correa"])
        for cuenta, apellido in ((self.santiago, "Cedeño"), (self.nicolas, "Correa")):
            self.assertEqual(cuenta.rol, Rol.TI)
            self.assertTrue(cuenta.protegida)
            self.assertTrue(cuenta.is_active)
            self.assertFalse(cuenta.is_superuser)
            self.assertEqual(cuenta.last_name, apellido)
            self.assertTrue(cuenta.groups.filter(name=Rol.TI.label).exists())

    def test_la_base_impide_borrarlas_desactivarlas_o_cambiarles_el_rol(self):
        for cambio in ({"is_active": False}, {"rol": Rol.VENDEDOR}, {"username": "otro.usuario"}, {"protegida": False}):
            with self.assertRaises((IntegrityError, InternalError)), transaction.atomic():
                Usuario.objects.filter(pk=self.santiago.pk).update(**cambio)
        with self.assertRaises((IntegrityError, InternalError)), transaction.atomic():
            Usuario.objects.filter(pk=self.nicolas.pk).delete()
        # La contraseña sí se puede cambiar.
        self.santiago.set_password("Otra-Clave-2026")
        self.santiago.save()

    def test_inicializar_datos_las_repone_si_faltan_en_una_base_nueva(self):
        call_command("inicializar_datos", verbosity=0)
        self.assertEqual(Usuario.objects.filter(username__in=["santiago.cedeno", "nicolas.correa"], protegida=True).count(), 2)

    def test_el_comando_genera_codigos_con_los_que_se_puede_ingresar(self):
        salida = StringIO()
        call_command("credenciales_ti", "santiago.cedeno", stdout=salida)
        codigo = salida.getvalue().split("código de acceso: ")[1].split()[0]
        self.client.logout()
        respuesta = self.client.post(reverse("login"), {"username": "santiago.cedeno", "password": codigo})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.santiago.pk)

    def test_el_comando_rechaza_cuentas_que_no_son_de_ti(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("credenciales_ti", "diego.mendoza", stdout=StringIO())

    def test_el_administrador_no_puede_tocar_las_cuentas_de_ti(self):
        self.client.force_login(self.superusuario)
        self.client.post(reverse("usuario_cambiar_estado", args=[self.santiago.pk]))
        self.client.post(reverse("usuario_restablecer_clave", args=[self.santiago.pk]))
        self.santiago.refresh_from_db()
        self.assertTrue(self.santiago.is_active)
        pagina = self.client.get(reverse("usuarios")).content.decode()
        self.assertIn("Cuenta de TI", pagina)
        formulario = self.client.get(reverse("usuario_crear")).content.decode()
        self.assertNotIn('value="TI"', formulario)  # no se crean cuentas de TI desde Usuarios


class AccesoTITests(BaseTI):
    def test_ti_ve_su_modulo_y_sus_reportes_y_nada_del_administrador(self):
        inicio = self.client.get(reverse("inicio")).content.decode()
        self.assertIn(reverse("ti:equipos"), inicio)
        for ruta in ("personal:empleado_lista", "nomina:resumen", "ventas:inicio", "cierre:bandeja", "asistencia:tablero",
                     "inventario:materiales", "usuarios", "agenda:calendario", "documentos:plantillas"):
            self.assertNotIn(reverse(ruta), inicio, ruta)
            self.assertIn(self.client.get(reverse(ruta)).status_code, (302, 403), ruta)
        for ruta in ("ti:equipos", "ti:crear", "ti:conectados", "reportes:centro"):
            self.assertEqual(self.client.get(reverse(ruta)).status_code, 200, ruta)
        centro = self.client.get(reverse("reportes:centro")).content.decode()
        self.assertIn("Inventario de equipos", centro)
        self.assertNotIn("Resumen de nómina", centro)

    def test_ni_el_administrador_ni_el_superusuario_ni_un_vendedor_entran_a_ti(self):
        for usuario in (self.admin, self.superusuario, self.vendedor):
            self.client.force_login(usuario)
            self.assertNotIn(reverse("ti:equipos"), self.client.get(reverse("inicio")).content.decode())
            for ruta in (reverse("ti:equipos"), reverse("ti:crear"), reverse("ti:conectados")):
                self.assertEqual(self.client.get(ruta).status_code, 403, (usuario, ruta))
        self.client.force_login(self.admin)
        self.assertNotIn("Inventario de equipos", self.client.get(reverse("reportes:centro")).content.decode())
        self.assertEqual(self.client.get(reverse("reportes:reporte", args=["TI_EQUIPOS"])).status_code, 403)

    def test_ti_no_marca_entrada_ni_ve_finalizar_dia_ni_almuerzo(self):
        self.client.logout()
        salida = StringIO()
        call_command("credenciales_ti", "nicolas.correa", stdout=salida)
        codigo = salida.getvalue().split("código de acceso: ")[1].split()[0]
        self.client.post(reverse("login"), {"username": "nicolas.correa", "password": codigo})
        self.assertFalse(RegistroAsistencia.objects.exists())
        inicio = self.client.get(reverse("inicio")).content.decode()
        self.assertNotIn("Finalizar día", inicio)
        self.assertNotIn("Salir a almorzar", inicio)
        self.assertNotIn("aviso-break", inicio)


class EquiposTests(BaseTI):
    def datos(self, **extra):
        return {"codigo": "", "tipo": "ESCRITORIO", "marca": "Dell", "modelo": "OptiPlex 3080", "numero_serie": "abc123",
                "procesador": "Core i5", "memoria_ram_gb": 8, "almacenamiento": "SSD 256 GB", "sistema_operativo": "Windows 11",
                "hostname": "call-pc-01", "direccion_ip": "192.168.1.21", "direccion_mac": "aa-bb-cc-dd-ee-01", "ubicacion": "Puesto 1",
                "asignado_a": "", "estado": "OPERATIVO", "fecha_compra": "2026-01-10", "proveedor": "Compu", "costo": "650.00",
                "garantia_hasta": "2027-01-10", "observacion": ""} | extra

    def test_registrar_equipo_asigna_codigo_y_anota_el_alta(self):
        respuesta = self.client.post(reverse("ti:crear"), self.datos(asignado_a=self.empleado.pk))
        equipo = Equipo.objects.get()
        self.assertRedirects(respuesta, reverse("ti:detalle", args=[equipo.pk]))
        self.assertEqual(equipo.codigo, "EQ-0001")
        self.assertEqual(equipo.numero_serie, "ABC123")
        self.assertEqual(equipo.direccion_mac, "AA:BB:CC:DD:EE:01")
        self.assertEqual(equipo.hostname, "CALL-PC-01")
        self.assertEqual(list(equipo.eventos.order_by("pk").values_list("tipo", flat=True)), ["ALTA", "ASIGNACION"])
        self.client.post(reverse("ti:crear"), self.datos(numero_serie="XYZ", direccion_ip="192.168.1.22", direccion_mac=""))
        self.assertTrue(Equipo.objects.filter(codigo="EQ-0002").exists())

    def test_cambiar_asignacion_y_estado_queda_en_la_hoja_de_vida(self):
        self.client.post(reverse("ti:crear"), self.datos(asignado_a=self.empleado.pk))
        equipo = Equipo.objects.get()
        self.client.post(reverse("ti:editar", args=[equipo.pk]), self.datos(codigo=equipo.codigo, estado="REPARACION"))
        tipos = list(equipo.eventos.order_by("pk").values_list("tipo", flat=True))
        self.assertEqual(tipos, ["ALTA", "ASIGNACION", "DEVOLUCION", "CAMBIO_ESTADO"])

    def test_validaciones_del_formulario(self):
        Equipo.objects.create(codigo="EQ-0001", tipo="LAPTOP", numero_serie="ABC123")
        respuesta = self.client.post(reverse("ti:crear"), self.datos(direccion_mac="zz", garantia_hasta="2025-01-01",
                                                                     estado="BAJA", asignado_a=self.empleado.pk))
        form = respuesta.context["form"]
        for campo in ("numero_serie", "direccion_mac", "garantia_hasta", "asignado_a"):
            self.assertIn(campo, form.errors, campo)

    def test_mantenimiento_queda_en_la_hoja_de_vida_y_no_se_puede_borrar(self):
        equipo = Equipo.objects.create(codigo="EQ-0001", tipo="LAPTOP")
        respuesta = self.client.post(reverse("ti:evento", args=[equipo.pk]), {
            "fecha": timezone.localdate().isoformat(), "tipo": "MANTENIMIENTO", "descripcion": "Limpieza y cambio de pasta térmica",
            "realizado_por": "Técnico externo", "costo": "25"})
        self.assertRedirects(respuesta, reverse("ti:detalle", args=[equipo.pk]))
        evento = EventoEquipo.objects.get(equipo=equipo)
        with self.assertRaises((IntegrityError, InternalError)), transaction.atomic():
            evento.delete()
        with self.assertRaises((IntegrityError, InternalError)), transaction.atomic():
            EventoEquipo.objects.filter(pk=evento.pk).update(descripcion="otra")

    def test_la_base_valida_el_equipo(self):
        for datos in ({"tipo": "NAVE"}, {"tipo": "LAPTOP", "costo": -1}, {"tipo": "LAPTOP", "estado": "PERDIDO"}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Equipo.objects.create(codigo="EQ-0099", **datos)


class ConexionesTests(BaseTI):
    def test_iniciar_y_cerrar_sesion_queda_registrado_con_ip_navegador_y_equipo(self):
        Equipo.objects.create(codigo="EQ-0001", tipo="ESCRITORIO", direccion_ip="127.0.0.1", ubicacion="Puesto 9")
        cliente = self.client_class(HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0")
        cliente.post(reverse("login"), {"username": "lucia.mora", "password": CLAVE})
        sesion = SesionEquipo.objects.get(usuario=self.vendedor)
        self.assertEqual((sesion.ip, sesion.navegador, sesion.sistema, sesion.dispositivo),
                         ("127.0.0.1", "Edge 130", "Windows 10/11", "Computador"))
        self.assertIn(sesion, servicios.conectados())
        pagina = self.client.get(reverse("ti:conectados")).content.decode()
        self.assertIn("Lucía Mora", pagina)
        self.assertIn("EQ-0001", pagina)
        cliente.post(reverse("logout"))
        sesion.refresh_from_db()
        self.assertEqual(sesion.motivo_fin, SesionEquipo.Fin.CIERRE)
        self.assertNotIn(sesion, servicios.conectados())

    def test_sin_actividad_reciente_ya_no_aparece_conectado(self):
        cliente = self.client_class()
        cliente.force_login(self.vendedor)
        sesion = SesionEquipo.objects.get(usuario=self.vendedor)
        SesionEquipo.objects.filter(pk=sesion.pk).update(ultima_actividad=timezone.now() - timedelta(minutes=30))
        self.assertNotIn(sesion, servicios.conectados())

    def test_ti_puede_cerrar_la_sesion_de_otro_equipo(self):
        cliente = self.client_class()
        cliente.force_login(self.vendedor)
        sesion = SesionEquipo.objects.get(usuario=self.vendedor)
        self.client.post(reverse("ti:cerrar_sesion", args=[sesion.pk]))
        sesion.refresh_from_db()
        self.assertEqual(sesion.motivo_fin, SesionEquipo.Fin.CERRADA_TI)
        self.assertEqual(sesion.cerrada_por, self.santiago)
        self.assertFalse(Session.objects.filter(session_key=sesion.clave_sesion).exists())
        self.assertEqual(cliente.get(reverse("inicio")).status_code, 302)  # tiene que volver a ingresar

    def test_detecta_celulares_y_otros_navegadores(self):
        movil = servicios.describir_agente("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36")
        self.assertEqual((movil["navegador"], movil["sistema"], movil["dispositivo"]), ("Chrome 129", "Android", "Celular"))
        firefox = servicios.describir_agente("Mozilla/5.0 (Windows NT 10.0; rv:131.0) Gecko/20100101 Firefox/131.0")
        self.assertEqual(firefox["navegador"], "Firefox 131")


class ReportesTITests(BaseTI):
    def test_los_tres_reportes_se_generan_en_pantalla_y_pdf(self):
        equipo = Equipo.objects.create(codigo="EQ-0001", tipo="LAPTOP", marca="HP", costo=500, estado="OPERATIVO")
        EventoEquipo.objects.create(equipo=equipo, fecha=timezone.localdate(), tipo="REPARACION", descripcion="Cambio de batería",
                                    costo=40, registrado_por=self.santiago)
        cliente = self.client_class()
        cliente.force_login(self.vendedor)
        for codigo, texto in (("TI_EQUIPOS", "EQ-0001"), ("TI_EVENTOS", "Cambio de batería"), ("TI_CONEXIONES", "lucia.mora")):
            pagina = self.client.get(reverse("reportes:reporte", args=[codigo]))
            self.assertContains(pagina, texto, msg_prefix=codigo)
            pdf = self.client.get(reverse("reportes:reporte_pdf", args=[codigo]))
            self.assertEqual(pdf["Content-Type"], "application/pdf", codigo)
        filtrado = self.client.get(reverse("reportes:reporte", args=["TI_EQUIPOS"]), {"tipo_equipo": "IMPRESORA"})
        self.assertNotContains(filtrado, "EQ-0001")
