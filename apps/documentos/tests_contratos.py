"""
4.1 Gestión documental › Contratos generados a partir de plantillas.

    python manage.py test apps.documentos
"""
import io
import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from apps.core.models import Rol, Usuario
from apps.personal.models import Cargo, Empleado, HistorialSalario

from . import contratos
from .models import ContratoGenerado, PlantillaContrato

PLANTILLA = """# CONTRATO DE PRUEBA

Yo, **{{nombre_completo}}**, con {{tipo_identificacion}} No. {{identificacion}}, domiciliado en {{direccion}},
trabajaré como {{cargo}} desde el {{fecha_inicio_texto}} por {{salario}} ({{salario_en_letras}}).

Nacido el {{fecha_nacimiento}}.

______________________
{{nombre_completo}}
"""


def texto_pdf(contenido: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(contenido)).pages)


class FormatosTests(TestCase):
    def test_numero_y_dinero_en_letras(self):
        self.assertEqual(contratos.numero_en_letras(482), "CUATROCIENTOS OCHENTA Y DOS")
        self.assertEqual(contratos.numero_en_letras(100), "CIEN")
        self.assertEqual(contratos.numero_en_letras(1021), "MIL VEINTIUNO")
        self.assertEqual(contratos.numero_en_letras(21000), "VEINTIÚN MIL")
        self.assertEqual(contratos.numero_en_letras(1_500_000), "UN MILLÓN QUINIENTOS MIL")
        self.assertEqual(contratos.dinero_en_letras(Decimal("482.5")), "CUATROCIENTOS OCHENTA Y DOS DÓLARES CON 50/100")
        self.assertEqual(contratos.dinero_en_letras(Decimal("1")), "UN DÓLAR CON 00/100")
        self.assertEqual(contratos.dinero_en_letras(Decimal("21")), "VEINTIÚN DÓLARES CON 00/100")
        self.assertEqual(contratos.dinero_en_letras(Decimal("531.25")), "QUINIENTOS TREINTA Y UN DÓLARES CON 25/100")

    def test_formatos_de_fecha_y_dinero(self):
        self.assertEqual(contratos.fecha_texto(date(2026, 11, 5)), "5 de noviembre de 2026")
        self.assertEqual(contratos.dinero(Decimal("1482")), "$ 1.482,00")

    def test_llenar_marca_faltantes_y_no_ejecuta_codigo(self):
        texto = contratos.llenar("Hola {{ nombres }} {{direccion}} {% debug %}", {"nombres": "Ana", "direccion": ""})
        self.assertEqual(texto, f"Hola Ana {contratos.FALTANTE} {{% debug %}}")
        # Un valor no puede activar formato (negrita) dentro del contrato.
        self.assertEqual(contratos.llenar("{{nombres}}", {"nombres": "**Ana**"}), "Ana")

    def test_variables_desconocidas(self):
        self.assertEqual(contratos.variables_desconocidas("{{nombres}} {{sueldo}}"), {"sueldo"})


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="callflow-test-"))
class ContratosVistasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        cls.admin = Usuario.objects.create_user("admin1", "admin1@callflow.test", "clave-segura-123", rol=Rol.ADMINISTRADOR)
        cls.vendedor = Usuario.objects.create_user("vend1", "vend1@callflow.test", "clave-segura-123", rol=Rol.VENDEDOR)
        cls.empleado = Empleado.objects.create(
            identificacion="1710034065", nombres="María José", apellidos="Pérez Ñañez", direccion="Av. Amazonas y Colón",
            cargo=Cargo.objects.get(nombre="Asesor de ventas"), fecha_ingreso=date(2026, 11, 2),
        )
        HistorialSalario.objects.create(empleado=cls.empleado, salario_base=Decimal("482.00"), vigente_desde=date(2026, 11, 1))
        cls.plantilla = PlantillaContrato.objects.create(nombre="Contrato prueba", contenido=PLANTILLA,
                                                         campos_variables=sorted(contratos.variables_en(PLANTILLA)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(self.admin)

    def datos(self, **cambios):
        return {"plantilla": self.plantilla.pk, "fecha_inicio": "2026-11-02", "fecha_fin": "", "salario": "482.00"} | cambios

    def test_plantilla_de_ejemplo_disponible(self):
        ejemplo = PlantillaContrato.objects.get(nombre__contains="EJEMPLO")
        self.assertTrue(ejemplo.activa)
        self.assertFalse(contratos.variables_desconocidas(ejemplo.contenido))

    def test_ficha_ofrece_generar_con_datos_sugeridos(self):
        ficha = self.client.get(reverse("personal:empleado_detalle", args=[self.empleado.pk]))
        self.assertContains(ficha, 'data-pestana="contratos"')
        form = ficha.context["contrato_form"]
        self.assertEqual(form.initial["fecha_inicio"], date(2026, 11, 2))
        self.assertEqual(form.initial["salario"], Decimal("482.00"))  # sale del historial de salario

    def test_vista_previa_con_datos_reales_y_aviso_de_faltantes(self):
        respuesta = self.client.post(reverse("documentos:contrato_vista_previa", args=[self.empleado.pk]), self.datos())
        self.assertContains(respuesta, "<strong>María José Pérez Ñañez</strong>", html=False)
        self.assertContains(respuesta, "CUATROCIENTOS OCHENTA Y DOS DÓLARES CON 00/100")
        self.assertContains(respuesta, "2 de noviembre de 2026")
        self.assertContains(respuesta, "fecha de nacimiento")  # la ficha no la tiene: se avisa
        self.assertFalse(ContratoGenerado.objects.exists())  # la vista previa no guarda nada

    def test_genera_pdf_y_lo_guarda_en_el_expediente(self):
        respuesta = self.client.post(reverse("documentos:contrato_generar", args=[self.empleado.pk]), self.datos())
        self.assertRedirects(respuesta, reverse("personal:empleado_detalle", args=[self.empleado.pk]) + "#contratos",
                             fetch_redirect_response=False)
        contrato = ContratoGenerado.objects.get()
        self.assertEqual(contrato.generado_por, self.admin)
        self.assertEqual(contrato.documento.tipo.codigo, "CONTRATO")
        self.assertEqual(contrato.documento.tipo_mime, "application/pdf")
        self.assertEqual(contrato.datos_utilizados["identificacion"], "1710034065")
        self.assertEqual(contrato.datos_utilizados["fecha_nacimiento"], "")
        texto = texto_pdf(contrato.documento.archivo.open("rb").read())
        for esperado in ("CONTRATO DE PRUEBA", "María José Pérez Ñañez", "1710034065", "Asesor de ventas", "$ 482,00"):
            self.assertIn(esperado, texto)
        self.assertIn(contratos.FALTANTE, texto)
        ficha = self.client.get(reverse("personal:empleado_detalle", args=[self.empleado.pk]))
        self.assertContains(ficha, reverse("documentos:ver", args=[contrato.documento.pk]))

    def test_contrato_a_plazo_exige_fecha_de_fin(self):
        Empleado.objects.filter(pk=self.empleado.pk).update(tipo_contrato="PLAZO_FIJO")
        self.client.post(reverse("documentos:contrato_generar", args=[self.empleado.pk]), self.datos())
        self.assertFalse(ContratoGenerado.objects.exists())
        self.client.post(reverse("documentos:contrato_generar", args=[self.empleado.pk]), self.datos(fecha_fin="2027-11-01"))
        self.assertEqual(ContratoGenerado.objects.get().fecha_fin, date(2027, 11, 1))

    def test_editar_plantilla_crea_version_y_conserva_la_anterior(self):
        self.client.post(reverse("documentos:contrato_generar", args=[self.empleado.pk]), self.datos())
        respuesta = self.client.post(reverse("documentos:plantilla_editar", args=[self.plantilla.pk]), {
            "nombre": "Contrato prueba", "tipo_contrato": "", "contenido": "Nuevo texto para {{nombre_completo}}",
        })
        self.assertRedirects(respuesta, reverse("documentos:plantillas"), fetch_redirect_response=False)
        versiones = PlantillaContrato.objects.filter(nombre="Contrato prueba").order_by("version")
        self.assertEqual([(p.version, p.activa) for p in versiones], [(1, False), (2, True)])
        self.assertEqual(ContratoGenerado.objects.get().plantilla.version, 1)  # el contrato viejo no cambia

    def test_plantilla_rechaza_variables_inexistentes(self):
        respuesta = self.client.post(reverse("documentos:plantilla_nueva"), {
            "nombre": "Otra", "tipo_contrato": "", "contenido": "Hola {{nombre_completo}}, sueldo {{sueldo}}",
        })
        self.assertContains(respuesta, "Variables que no existen")
        self.assertFalse(PlantillaContrato.objects.filter(nombre="Otra").exists())

    def test_vista_previa_de_plantilla_usa_datos_de_ejemplo(self):
        respuesta = self.client.post(reverse("documentos:plantilla_vista_previa"), {"contenido": "# Título\n\nHola {{nombre_completo}}"})
        self.assertContains(respuesta, '<h3 class="titulo-contrato">Título</h3>', html=False)
        self.assertContains(respuesta, "María José Pérez Ñañez")

    def test_vista_previa_escapa_html(self):
        respuesta = self.client.post(reverse("documentos:plantilla_vista_previa"), {"contenido": "<script>alert(1)</script> {{nombres}}"})
        self.assertNotContains(respuesta, "<script>alert(1)</script>", html=False)

    def test_vendedor_no_genera_contratos_ni_ve_plantillas(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.post(reverse("documentos:contrato_generar", args=[self.empleado.pk]), self.datos()).status_code, 403)
        self.assertEqual(self.client.get(reverse("documentos:plantillas")).status_code, 403)
