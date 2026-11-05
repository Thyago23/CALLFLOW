"""
Registro de empleados con documentos de ingreso, lectura del CV y cuenta de acceso automática.

    python manage.py test apps.personal
"""
import json
import shutil
import tempfile
from datetime import date

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.cuentas import rol_para_cargo
from apps.core.models import Rol, Usuario
from apps.documentos.models import Documento

from . import cv
from .models import Cargo, Empleado
from .tests import CEDULA_VALIDA, OTRA_CEDULA_VALIDA, datos_registro, pdf_falso

CV_TEXTO = """
CURRICULUM VITAE
Nicolás Andrés Correa Fajardo
DATOS PERSONALES
Cédula: 171003406-5
Fecha de nacimiento: 14/03/1998
Dirección: Av. 10 de Agosto N24-15 y Colón, Quito
Celular: +593 99 876 5432
Correo: Nicolas.Correa@Correo.com
EXPERIENCIA LABORAL
Asesor comercial · 2019-2023
"""


def pdf_con_texto(lineas: list[str]) -> bytes:
    """PDF mínimo pero válido con una línea de texto por renglón (para probar la lectura real)."""
    def escapar(t):
        return t.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    contenido = b"BT /F1 11 Tf 50 780 Td 14 TL " + b" ".join(b"(" + escapar(l) + b") '" for l in lineas) + b" ET"
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(contenido) + contenido + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    salida, posiciones = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    salida += b"".join(b"%010d 00000 n \n" % p for p in posiciones)
    salida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, xref)
    return bytes(salida)


# --------------------------------------------------------------------------- lectura del CV (reglas)
class LecturaCVTests(TestCase):
    def test_extrae_todos_los_campos_de_un_cv_tipico(self):
        datos = cv.extraer_datos(CV_TEXTO)
        self.assertEqual(datos["nombres"], "Nicolás Andrés")
        self.assertEqual(datos["apellidos"], "Correa Fajardo")
        self.assertEqual(datos["tipo_identificacion"], "CEDULA")
        self.assertEqual(datos["identificacion"], CEDULA_VALIDA)
        self.assertEqual(datos["fecha_nacimiento"], "1998-03-14")
        self.assertEqual(datos["telefono"], "0998765432")
        self.assertEqual(datos["email"], "nicolas.correa@correo.com")
        self.assertEqual(datos["direccion"], "Av. 10 de Agosto N24-15 y Colón, Quito")

    def test_nombres_con_etiquetas_separadas(self):
        datos = cv.extraer_datos("Hoja de vida\nNombres: María José\nApellidos: Pérez Ñañez\n")
        self.assertEqual((datos["nombres"], datos["apellidos"]), ("María José", "Pérez Ñañez"))

    def test_division_del_nombre_completo(self):
        self.assertEqual(cv.dividir_nombre_completo("Juan Pérez"), ("Juan", "Pérez"))
        self.assertEqual(cv.dividir_nombre_completo("Juan Pérez Gómez"), ("Juan", "Pérez Gómez"))
        self.assertEqual(cv.dividir_nombre_completo("María de los Ángeles Vera Mora"), ("María de los Ángeles", "Vera Mora"))

    def test_fecha_de_nacimiento_en_texto(self):
        datos = cv.extraer_datos("Ana Ruiz\nFecha de nacimiento: 5 de julio de 1990\n")
        self.assertEqual(datos["fecha_nacimiento"], "1990-07-05")

    def test_ignora_cedula_invalida_y_no_confunde_celular_con_cedula(self):
        datos = cv.extraer_datos("Ana Ruiz\nCédula: 1234567890\nCelular: 0991234567\n")
        self.assertNotIn("identificacion", datos)
        self.assertEqual(datos["telefono"], "0991234567")

    def test_formato_de_la_empresa_con_datos_separados_por_barras(self):
        """Formato estándar: nombre, cargo, contacto con '|' y una línea de datos personales."""
        texto = (
            "ANA LUCÍA VERA MORA\nASESORA COMERCIAL | ATENCIÓN AL CLIENTE\n"
            "Quito, Ecuador | +593 991234567 | ana.vera@correo.com\n"
            f"Cédula: {CEDULA_VALIDA} | Fecha de nacimiento: 14/03/1998 | Dirección: Av. 10 de Agosto N24-15 y Colón, Quito\n"
            "PERFIL PROFESIONAL\nAsesora con experiencia en ventas telefónicas.\n"
        )
        datos = cv.extraer_datos(texto)
        self.assertEqual((datos["nombres"], datos["apellidos"]), ("Ana Lucía", "Vera Mora"))
        self.assertEqual(datos["identificacion"], CEDULA_VALIDA)
        self.assertEqual(datos["fecha_nacimiento"], "1998-03-14")
        self.assertEqual(datos["telefono"], "0991234567")
        self.assertEqual(datos["email"], "ana.vera@correo.com")
        self.assertEqual(datos["direccion"], "Av. 10 de Agosto N24-15 y Colón, Quito")

    def test_sin_direccion_usa_la_ciudad_del_encabezado(self):
        texto = "LUIS ANDRADE PAZ\nVENDEDOR\nGuayaquil, Ecuador | +593 987654321 | luis@correo.com\nPERFIL PROFESIONAL\n"
        datos = cv.extraer_datos(texto)
        self.assertEqual(datos["direccion"], "Guayaquil, Ecuador")
        self.assertEqual(datos["telefono"], "0987654321")
        self.assertNotIn("identificacion", datos)  # el CV no trae cédula: no se inventa

    def test_cv_sin_datos_reconocibles(self):
        self.assertEqual(cv.extraer_datos("EXPERIENCIA\n2019-2023 ventas\n"), {})

    def test_lee_un_pdf_real(self):
        archivo = SimpleUploadedFile("cv.pdf", pdf_con_texto(CV_TEXTO.replace("·", "-").strip().splitlines()))
        datos = cv.leer_cv(archivo)
        self.assertEqual(datos["identificacion"], CEDULA_VALIDA)
        self.assertEqual(datos["apellidos"], "Correa Fajardo")

    def test_pdf_sin_texto_es_ilegible(self):
        with self.assertRaises(cv.CVIlegible):
            cv.leer_cv(SimpleUploadedFile("escaneado.pdf", pdf_con_texto([])))
        with self.assertRaises(cv.CVIlegible):
            cv.leer_cv(SimpleUploadedFile("roto.pdf", b"%PDF-1.4 basura"))


# --------------------------------------------------------------------------- vistas
@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="callflow-test-"))
class RegistroConDocumentosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin1", "admin1@callflow.test", "clave-segura-123", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vend1", "vend1@callflow.test", "clave-segura-123", rol=Rol.VENDEDOR)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(self.admin)

    def registrar(self, **cambios):
        return self.client.post(reverse("personal:empleado_crear"), datos_registro(**cambios))

    # ----------------------------------------------------------- documentos
    def test_guarda_los_tres_documentos_en_el_expediente(self):
        self.registrar()
        empleado = Empleado.objects.get()
        documentos = {d.tipo.codigo: d for d in empleado.documentos.select_related("tipo")}
        self.assertEqual(set(documentos), {"HOJA_VIDA", "CEDULA", "TITULO_BACHILLER"})
        cv_guardado = documentos["HOJA_VIDA"]
        self.assertEqual(cv_guardado.tipo_mime, "application/pdf")
        self.assertEqual(cv_guardado.nombre_original, "cv.pdf")
        self.assertEqual(cv_guardado.subido_por, self.admin)
        self.assertRegex(cv_guardado.hash_sha256, r"^[0-9a-f]{64}$")
        # El nombre en disco es aleatorio: no expone datos personales.
        self.assertNotIn("cv", cv_guardado.archivo.name.rsplit("/", 1)[-1])

    def test_documentos_son_obligatorios(self):
        datos = datos_registro()
        del datos["titulo_bachiller"]
        respuesta = self.client.post(reverse("personal:empleado_crear"), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "vuelva a adjuntar los documentos")
        self.assertFalse(Empleado.objects.exists())

    def test_rechaza_archivo_que_no_es_pdf_ni_imagen_aunque_se_llame_pdf(self):
        falso = SimpleUploadedFile("cedula.pdf", b"MZ\x90\x00 ejecutable", content_type="application/pdf")
        respuesta = self.client.post(reverse("personal:empleado_crear"), datos_registro() | {"cedula": falso})
        self.assertContains(respuesta, "Solo se aceptan archivos PDF, JPG o PNG")
        self.assertFalse(Empleado.objects.exists())

    def test_acepta_imagenes(self):
        png = SimpleUploadedFile("cedula.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, content_type="image/png")
        self.client.post(reverse("personal:empleado_crear"), datos_registro() | {"cedula": png})
        self.assertEqual(Documento.objects.get(tipo__codigo="CEDULA").tipo_mime, "image/png")

    def test_rechaza_el_mismo_archivo_en_dos_documentos(self):
        respuesta = self.client.post(reverse("personal:empleado_crear"), datos_registro() | {"titulo_bachiller": pdf_falso("cv")})
        self.assertContains(respuesta, "Es el mismo archivo que adjuntó")
        self.assertFalse(Empleado.objects.exists())

    # ----------------------------------------------------------- cuenta automática
    def test_crea_usuario_y_codigo_y_permite_ingresar(self):
        respuesta = self.registrar()
        self.assertRedirects(respuesta, reverse("usuario_credenciales"), fetch_redirect_response=False)
        empleado = Empleado.objects.select_related("usuario").get()
        usuario = empleado.usuario
        self.assertEqual(usuario.username, "maria.perez")
        self.assertEqual(usuario.rol, Rol.VENDEDOR)
        self.assertEqual(usuario.email, "maria.perez@correo.com")
        self.assertEqual(usuario.creado_por, self.admin)

        credenciales = self.client.get(reverse("usuario_credenciales"))
        codigo = credenciales.context["clave"]
        self.assertRegex(codigo, r"^MP\d{8}$")
        self.assertContains(credenciales, reverse("personal:empleado_detalle", args=[empleado.pk]))
        # El código solo se muestra una vez.
        self.assertRedirects(self.client.get(reverse("usuario_credenciales")), reverse("usuarios"), fetch_redirect_response=False)

        otro = Client()
        ingreso = otro.post(reverse("login"), {"username": "maria.perez", "password": codigo})
        self.assertRedirects(ingreso, reverse("inicio"), fetch_redirect_response=False)

    def test_usuarios_unicos_para_homonimos_y_correo_repetido(self):
        self.registrar()
        self.client.get(reverse("usuario_credenciales"))
        self.registrar(identificacion=OTRA_CEDULA_VALIDA)  # mismo nombre y correo, otra cédula
        usuarios = sorted(Empleado.objects.values_list("usuario__username", flat=True))
        self.assertEqual(usuarios, ["maria.perez", "maria.perez2"])
        segundo = Usuario.objects.get(username="maria.perez2")
        self.assertIsNone(segundo.email)  # el correo ya lo usa la primera cuenta

    def test_rol_segun_cargo(self):
        self.assertEqual(rol_para_cargo("Administrador"), Rol.ADMINISTRADOR)
        self.assertEqual(rol_para_cargo("Encargado de cierre"), Rol.ENCARGADO_CIERRE)
        self.assertEqual(rol_para_cargo("Vendedor telefónico"), Rol.VENDEDOR)
        self.assertEqual(rol_para_cargo("Cargo nuevo"), Rol.VENDEDOR)
        cierre = Cargo.objects.get(nombre="Encargado de cierre")
        self.registrar(cargo=cierre.pk)
        self.assertEqual(Empleado.objects.get().usuario.rol, Rol.ENCARGADO_CIERRE)

    def test_si_falla_algo_no_queda_nada_a_medias(self):
        # Sin el tipo de documento en el catálogo, el guardado falla y se revierte todo.
        from apps.documentos.models import TipoDocumento
        TipoDocumento.objects.filter(codigo="TITULO_BACHILLER").delete()
        with self.assertRaises(TipoDocumento.DoesNotExist):
            self.registrar()
        self.assertFalse(Empleado.objects.exists())
        self.assertFalse(Usuario.objects.filter(username="maria.perez").exists())

    # ----------------------------------------------------------- lectura del CV en vivo
    def test_leer_cv_devuelve_los_campos_para_el_formulario(self):
        archivo = SimpleUploadedFile("cv.pdf", pdf_con_texto(CV_TEXTO.replace("·", "-").strip().splitlines()))
        respuesta = self.client.post(reverse("personal:leer_cv"), {"hoja_vida": archivo}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Se llenaron desde el CV")
        campos = json.loads(respuesta["HX-Trigger"])["cvLeido"]["campos"]
        self.assertEqual(campos["identificacion"], CEDULA_VALIDA)
        self.assertEqual(campos["nombres"], "Nicolás Andrés")
        self.assertEqual(campos["fecha_nacimiento"], "1998-03-14")
        respuesta["HX-Trigger"].encode("ascii")  # la cabecera no lleva caracteres fuera de ASCII

    def test_leer_cv_imagen_o_escaneado_avisa(self):
        png = SimpleUploadedFile("cv.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        respuesta = self.client.post(reverse("personal:leer_cv"), {"hoja_vida": png})
        self.assertContains(respuesta, "es una imagen")
        self.assertNotIn("HX-Trigger", respuesta)
        escaneado = SimpleUploadedFile("cv.pdf", pdf_con_texto([]))
        self.assertContains(self.client.post(reverse("personal:leer_cv"), {"hoja_vida": escaneado}), "parece escaneado")

    def test_leer_cv_requiere_permiso(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.post(reverse("personal:leer_cv"), {"hoja_vida": pdf_falso("cv")}).status_code, 403)

    # ----------------------------------------------------------- ficha del empleado
    def test_ficha_muestra_datos_documentos_y_cuenta(self):
        self.registrar()
        empleado = Empleado.objects.get()
        ficha = self.client.get(reverse("personal:empleado_detalle", args=[empleado.pk]))
        self.assertContains(ficha, "maria.perez")
        self.assertContains(ficha, "Título de bachiller")
        self.assertContains(ficha, "Generar nuevo código de acceso")
        self.assertNotContains(ficha, "Falta en el expediente")
        documento = empleado.documentos.get(tipo__codigo="CEDULA")
        archivo = self.client.get(reverse("personal:documento_ver", args=[documento.pk]))
        self.assertEqual(archivo["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(archivo.streaming_content), pdf_falso("cedula").read())

    def test_vendedor_no_ve_documentos(self):
        self.registrar()
        documento = Documento.objects.first()
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(reverse("personal:documento_ver", args=[documento.pk])).status_code, 403)

    def test_generar_nuevo_codigo_desde_la_ficha(self):
        self.registrar()
        self.client.get(reverse("usuario_credenciales"))
        empleado = Empleado.objects.select_related("usuario").get()
        ficha = reverse("personal:empleado_detalle", args=[empleado.pk])
        self.client.post(reverse("usuario_restablecer_clave", args=[empleado.usuario.pk]), {"volver": ficha})
        credenciales = self.client.get(reverse("usuario_credenciales"))
        self.assertContains(credenciales, "Nuevo código de acceso")
        self.assertContains(credenciales, f'href="{ficha}"')
        empleado.usuario.refresh_from_db()
        self.assertTrue(empleado.usuario.check_password(credenciales.context["clave"]))

    def test_volver_no_acepta_sitios_externos(self):
        self.registrar()
        self.client.get(reverse("usuario_credenciales"))
        usuario = Empleado.objects.get().usuario
        self.client.post(reverse("usuario_restablecer_clave", args=[usuario.pk]), {"volver": "https://sitio-malo.com/"})
        self.assertNotContains(self.client.get(reverse("usuario_credenciales")), "sitio-malo")

    def test_crear_cuenta_a_empleado_antiguo_sin_usuario(self):
        empleado = Empleado.objects.create(
            identificacion=OTRA_CEDULA_VALIDA, nombres="Luis", apellidos="Andrade",
            cargo=Cargo.objects.get(nombre="Vendedor telefónico"), fecha_ingreso=date.today(),
        )
        ficha = self.client.get(reverse("personal:empleado_detalle", args=[empleado.pk]))
        self.assertContains(ficha, "Crear usuario y código de acceso")
        self.assertContains(ficha, "Falta en el expediente")
        self.client.post(reverse("personal:empleado_crear_cuenta", args=[empleado.pk]))
        empleado.refresh_from_db()
        self.assertEqual(empleado.usuario.username, "luis.andrade")
        # Repetirlo no crea una segunda cuenta.
        self.client.post(reverse("personal:empleado_crear_cuenta", args=[empleado.pk]))
        self.assertEqual(Usuario.objects.filter(username__startswith="luis.andrade").count(), 1)


# --------------------------------------------------------------------------- ficha con pestañas y observaciones
@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="callflow-test-"))
class FichaPestanasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin1", "admin1@callflow.test", "clave-segura-123", rol=Rol.ADMINISTRADOR,
                                                first_name="Ana", last_name="Admin")
        cls.vendedor = Usuario.objects.create_user("vend1", "vend1@callflow.test", "clave-segura-123", rol=Rol.VENDEDOR)
        cls.empleado = Empleado.objects.create(
            identificacion=CEDULA_VALIDA, nombres="Luis", apellidos="Andrade",
            cargo=Cargo.objects.get(nombre="Vendedor telefónico"), fecha_ingreso=date.today(),
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("personal:empleado_detalle", args=[self.empleado.pk])

    def test_ficha_tiene_las_pestanas(self):
        ficha = self.client.get(self.url)
        for pestana in ("datos", "documentos", "asistencia", "observaciones", "cuenta"):
            self.assertContains(ficha, f'data-pestana="{pestana}"')
            self.assertContains(ficha, f'id="{pestana}" role="tabpanel"')

    def test_agrega_observacion_con_autor_y_vuelve_a_la_pestana(self):
        respuesta = self.client.post(reverse("personal:observacion_crear", args=[self.empleado.pk]),
                                     {"tipo": "LLAMADO_ATENCION", "texto": "  Llegó tarde tres veces esta semana.  "})
        self.assertRedirects(respuesta, self.url + "#observaciones", fetch_redirect_response=False)
        observacion = self.empleado.observaciones.get()
        self.assertEqual(observacion.texto, "Llegó tarde tres veces esta semana.")
        self.assertEqual(observacion.autor, self.admin)
        ficha = self.client.get(self.url)
        self.assertContains(ficha, "Llegó tarde tres veces esta semana.")
        self.assertContains(ficha, "Llamado de atención")
        self.assertContains(ficha, "Ana Admin")

    def test_observacion_vacia_no_se_guarda(self):
        self.client.post(reverse("personal:observacion_crear", args=[self.empleado.pk]), {"tipo": "GENERAL", "texto": "   "})
        self.assertFalse(self.empleado.observaciones.exists())

    def test_observaciones_no_se_modifican_ni_borran(self):
        from django.db import DatabaseError, transaction
        from .models import ObservacionEmpleado
        o = ObservacionEmpleado.objects.create(empleado=self.empleado, texto="Buen desempeño", tipo="POSITIVA", autor=self.admin)
        for accion in (lambda: ObservacionEmpleado.objects.filter(pk=o.pk).update(texto="otro"),
                       lambda: ObservacionEmpleado.objects.filter(pk=o.pk).delete()):
            with self.assertRaises(DatabaseError), transaction.atomic():
                accion()

    def test_vendedor_no_puede_agregar_observaciones(self):
        self.client.force_login(self.vendedor)
        respuesta = self.client.post(reverse("personal:observacion_crear", args=[self.empleado.pk]), {"tipo": "GENERAL", "texto": "x"})
        self.assertEqual(respuesta.status_code, 403)
