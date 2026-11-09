"""
SCRUM-12 · Registrar nuevos empleados.

Criterios de aceptación:
  1. El formulario permite ingresar datos personales y laborales.
  2. Los campos obligatorios se validan antes de guardar.
  3. No se permite registrar dos empleados con la misma identificación.
  4. El empleado registrado aparece en el listado del personal.
"""
from datetime import date, timedelta

import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import RegistroCambio, Rol, Usuario
from apps.core.validadores import cedula_valida, ruc_persona_natural_valido

from .forms import EmpleadoForm
from .models import Cargo, Empleado

CEDULA_VALIDA = "1710034065"
OTRA_CEDULA_VALIDA = "0926687856"


def datos_validos(**cambios):
    datos = {
        "tipo_identificacion": "CEDULA",
        "identificacion": CEDULA_VALIDA,
        "nombres": "maría  josé",
        "apellidos": "PÉREZ ñañez",
        "fecha_nacimiento": "1995-04-20",
        "telefono": "099 123 4567",
        "email": "Maria.Perez@Correo.com",
        "direccion": "Av. Amazonas   y Colón",
        "cargo": Cargo.objects.get(nombre="Vendedor telefónico").pk,
        "tipo_contrato": "INDEFINIDO",
        "fecha_ingreso": date.today().isoformat(),
    }
    datos.update(cambios)
    return datos


def pdf_falso(texto: str) -> SimpleUploadedFile:
    """Archivo con firma de PDF; basta para la validación por contenido (no se lee como CV)."""
    contenido = f"%PDF-1.4\n% {texto}\n%%EOF\n".encode()
    return SimpleUploadedFile(f"{texto}.pdf", contenido, content_type="application/pdf")


def datos_registro(**cambios):
    """Datos del formulario + los tres documentos de ingreso obligatorios."""
    return datos_validos(**cambios) | {
        "hoja_vida": pdf_falso("cv"), "cedula": pdf_falso("cedula"), "titulo_bachiller": pdf_falso("titulo"),
    }


class ValidadoresTests(TestCase):
    def test_cedula(self):
        self.assertTrue(cedula_valida(CEDULA_VALIDA))
        self.assertTrue(cedula_valida(OTRA_CEDULA_VALIDA))
        self.assertFalse(cedula_valida("1710034066"))  # dígito verificador alterado
        self.assertFalse(cedula_valida("9910034065"))  # provincia inexistente
        self.assertFalse(cedula_valida("171003406"))  # 9 dígitos

    def test_ruc_persona_natural(self):
        self.assertTrue(ruc_persona_natural_valido(CEDULA_VALIDA + "001"))
        self.assertFalse(ruc_persona_natural_valido(CEDULA_VALIDA + "000"))


class EmpleadoFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)

    def test_criterio1_registra_datos_personales_y_laborales_normalizados(self):
        form = EmpleadoForm(data=datos_validos())
        self.assertTrue(form.is_valid(), form.errors)
        empleado = form.save()
        self.assertEqual(empleado.nombres, "María José")
        self.assertEqual(empleado.apellidos, "Pérez Ñañez")
        self.assertEqual(empleado.telefono, "0991234567")
        self.assertEqual(empleado.email, "maria.perez@correo.com")
        self.assertEqual(empleado.direccion, "Av. Amazonas y Colón")
        self.assertEqual(empleado.cargo.nombre, "Vendedor telefónico")
        self.assertEqual(empleado.estado, Empleado.Estado.ACTIVO)

    def test_criterio2_campos_obligatorios(self):
        form = EmpleadoForm(data={})
        self.assertFalse(form.is_valid())
        obligatorios = {"tipo_identificacion", "identificacion", "nombres", "apellidos", "telefono",
                        "cargo", "tipo_contrato", "fecha_ingreso"}
        self.assertEqual(obligatorios, set(form.errors))
        self.assertIn("is-invalid", form.fields["nombres"].widget.attrs["class"])

    def test_criterio2_formatos_invalidos(self):
        casos = {
            "identificacion": "1710034066",
            "nombres": "Juan123",
            "telefono": "abc",
            "email": "no-es-correo",
            "fecha_nacimiento": (date.today() + timedelta(days=1)).isoformat(),
            "fecha_ingreso": (date.today() + timedelta(days=400)).isoformat(),
        }
        for campo, valor in casos.items():
            with self.subTest(campo=campo):
                form = EmpleadoForm(data=datos_validos(**{campo: valor}))
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

    def test_criterio2_ingreso_antes_de_nacer(self):
        form = EmpleadoForm(data=datos_validos(fecha_nacimiento="2000-01-01", fecha_ingreso="1999-01-01"))
        self.assertFalse(form.is_valid())
        self.assertIn("fecha_ingreso", form.errors)

    def test_criterio2_cargo_inactivo_no_seleccionable(self):
        inactivo = Cargo.objects.create(nombre="Cargo antiguo", activo=False)
        form = EmpleadoForm(data=datos_validos(cargo=inactivo.pk))
        self.assertFalse(form.is_valid())
        self.assertIn("cargo", form.errors)

    def test_criterio3_identificacion_duplicada(self):
        EmpleadoForm(data=datos_validos()).save()
        # Mismo número escrito con espacios y guiones: se normaliza y se detecta igual.
        form = EmpleadoForm(data=datos_validos(identificacion="171-003-4065 ", nombres="Otra", apellidos="Persona"))
        self.assertFalse(form.is_valid())
        self.assertIn("Ya existe un empleado registrado", form.errors["identificacion"][0])
        self.assertEqual(Empleado.objects.count(), 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="callflow-test-"))
class EmpleadoVistasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin1", "admin1@callflow.test", "clave-segura-123", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vend1", "vend1@callflow.test", "clave-segura-123", rol=Rol.VENDEDOR)

    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def test_rol_asigna_grupo_de_permisos(self):
        self.assertTrue(self.admin.has_perm("personal.add_empleado"))
        self.assertFalse(self.vendedor.has_perm("personal.add_empleado"))

    def test_requiere_iniciar_sesion(self):
        respuesta = self.client.get(reverse("personal:empleado_crear"))
        self.assertRedirects(respuesta, f"{reverse('login')}?next={reverse('personal:empleado_crear')}")

    def test_vendedor_no_puede_registrar(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(reverse("personal:empleado_crear")).status_code, 403)
        self.assertEqual(self.client.post(reverse("personal:empleado_crear"), datos_registro()).status_code, 403)
        self.assertEqual(Empleado.objects.count(), 0)

    def test_vincula_una_cuenta_existente_sin_crear_otra(self):
        marlon = Usuario.objects.create_user("marlon.prue", None, "clave-segura-123", first_name="Marlon", last_name="Prue",
                                             rol=Rol.VENDEDOR)
        self.client.force_login(self.admin)
        formulario = self.client.get(reverse("personal:empleado_crear"))
        self.assertContains(formulario, "marlon.prue")
        datos = datos_registro(nombres="Marlon", apellidos="Prue", cuenta_existente=marlon.pk,
                               cargo=Cargo.objects.get(nombre="Encargado de cierre").pk)
        respuesta = self.client.post(reverse("personal:empleado_crear"), datos)
        empleado = Empleado.objects.get(identificacion=CEDULA_VALIDA)
        self.assertRedirects(respuesta, reverse("personal:empleado_detalle", args=[empleado.pk]), fetch_redirect_response=False)
        marlon.refresh_from_db()
        self.assertEqual((empleado.usuario, marlon.rol), (marlon, Rol.ENCARGADO_CIERRE))  # rol según el cargo
        self.assertTrue(marlon.check_password("clave-segura-123"))  # conserva su código
        self.assertFalse(Usuario.objects.filter(username__startswith="marlon.prue").exclude(pk=marlon.pk).exists())
        # Una cuenta ya vinculada no se ofrece ni se acepta para otro empleado.
        self.assertNotContains(self.client.get(reverse("personal:empleado_crear")), "marlon.prue")
        otro = self.client.post(reverse("personal:empleado_crear"), datos_registro(identificacion=OTRA_CEDULA_VALIDA, cuenta_existente=marlon.pk))
        self.assertIn("cuenta_existente", otro.context["form"].errors)

    def test_criterio4_registro_completo_y_aparece_en_listado(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("personal:empleado_crear")), "Registrar empleado")

        respuesta = self.client.post(reverse("personal:empleado_crear"), datos_registro(), follow=True)
        empleado = Empleado.objects.get(identificacion=CEDULA_VALIDA)
        # Tras guardar se muestran las credenciales generadas; "Listo" lleva a la ficha del empleado.
        self.assertRedirects(respuesta, reverse("usuario_credenciales"))
        self.assertContains(respuesta, "registrado con sus documentos")
        self.assertContains(respuesta, reverse("personal:empleado_detalle", args=[empleado.pk]))
        listado = self.client.get(reverse("personal:empleado_lista"))
        self.assertContains(listado, "Pérez Ñañez María José")
        # El alta queda en la bitácora de auditoría con el usuario que la hizo (sección 6).
        self.assertTrue(RegistroCambio.objects.filter(
            tabla="personal_empleado", registro_id=str(empleado.pk), accion="INSERT", usuario_id=self.admin.pk
        ).exists())

    def test_criterio3_duplicado_desde_la_vista(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("personal:empleado_crear"), datos_registro())
        respuesta = self.client.post(reverse("personal:empleado_crear"), datos_registro(nombres="Otro"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Ya existe un empleado registrado con esta identificación")
        self.assertEqual(Empleado.objects.count(), 1)

    def test_busqueda_htmx_devuelve_solo_la_tabla(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("personal:empleado_crear"), datos_registro())
        self.client.post(reverse("personal:empleado_crear"), datos_registro(
            identificacion=OTRA_CEDULA_VALIDA, nombres="Luis", apellidos="Andrade"))
        respuesta = self.client.get(reverse("personal:empleado_lista"), {"q": "andrade"}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Andrade Luis")
        self.assertNotContains(respuesta, "Pérez")
        self.assertNotContains(respuesta, "<html")

    def test_verificacion_en_vivo_de_identificacion(self):
        self.client.force_login(self.admin)
        url = reverse("personal:verificar_identificacion")
        self.assertContains(self.client.get(url, {"tipo_identificacion": "CEDULA", "identificacion": CEDULA_VALIDA}), "disponible")
        self.assertContains(self.client.get(url, {"tipo_identificacion": "CEDULA", "identificacion": "123"}), "no es válida")
        self.client.post(reverse("personal:empleado_crear"), datos_registro())
        self.assertContains(self.client.get(url, {"tipo_identificacion": "CEDULA", "identificacion": CEDULA_VALIDA}), "Ya está registrada")
