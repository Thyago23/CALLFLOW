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


class PlantillasDeLaEmpresaTests(TestCase):
    """Contratos de servicios profesionales (asesores de ventas) e imagen corporativa, con el formato de sus Word."""

    @classmethod
    def setUpTestData(cls):
        from apps.documentos.models import PlantillaContrato
        from apps.personal.models import Cargo, Empleado

        call_command("inicializar_datos", verbosity=0)
        cls.asesor = Empleado.objects.create(identificacion="1721129789", nombres="Jonathan Rodrigo", apellidos="Quintana Chamba",
                                             cargo=Cargo.objects.get(nombre="Asesor de ventas"), fecha_ingreso=date(2026, 11, 5))
        cls.imagen = Empleado.objects.create(identificacion="0930556048", nombres="María José", apellidos="Pérez Ñañez",
                                             cargo=Cargo.objects.get(nombre="Imagen corporativa"), fecha_ingreso=date(2026, 11, 9))
        cls.servicios = PlantillaContrato.objects.get(nombre="Contrato de servicios profesionales bajo comisiones")
        cls.imagen_corporativa = PlantillaContrato.objects.get(nombre="Contrato de servicios profesionales de imagen corporativa y visual")

    def test_cada_cargo_propone_su_plantilla(self):
        from apps.documentos.forms import GenerarContratoForm

        self.assertEqual(GenerarContratoForm(empleado=self.asesor).initial["plantilla"], self.servicios)
        self.assertEqual(GenerarContratoForm(empleado=self.imagen).initial["plantilla"], self.imagen_corporativa)

    def test_servicios_profesionales_se_llena_con_la_ficha(self):
        datos = contratos.datos_del_empleado(self.asesor, fecha_inicio=date(2026, 11, 5), fecha_fin=None, salario=Decimal("400"))
        texto = contratos.llenar(self.servicios.contenido, datos)
        self.assertEqual(contratos.faltantes(self.servicios.contenido, datos), [])
        for esperado in ("**QUINTANA CHAMBA JONATHAN RODRIGO**", "pasaporte Nº **1721129789**", "retribución económica de $400,",
                         "| Pago Base $400 |", "Quito D.M., 05 de noviembre del 2026.", "**CÉDULA Nº 1721129789**"):
            self.assertIn(esperado, texto)
        pdf = PdfReader(io.BytesIO(contratos.generar_pdf(texto, estilo=self.servicios.estilo)))
        self.assertIn("QUINTANA CHAMBA JONATHAN", pdf.pages[0].extract_text())

    def test_imagen_corporativa_se_llena_con_la_ficha(self):
        datos = contratos.datos_del_empleado(self.imagen, fecha_inicio=date(2026, 11, 9), fecha_fin=date(2027, 2, 8), salario=Decimal("300"))
        texto = contratos.llenar(self.imagen_corporativa.contenido, datos)
        self.assertEqual(contratos.faltantes(self.imagen_corporativa.contenido, datos), [])
        for esperado in ("a los 09 días del mes de noviembre del año dos mil veintiséis", "la señorita PÉREZ ÑAÑEZ MARÍA JOSÉ portadora",
                         "pasaporte Nº 093055604-8", "duración de 3 meses", "$300 trescientos dólares de los Estados Unidos de América",
                         "del año 2026."):
            self.assertIn(esperado, texto)
        pdf = PdfReader(io.BytesIO(contratos.generar_pdf(texto, estilo=self.imagen_corporativa.estilo)))
        self.assertIn("093055604-8", "".join(p.extract_text() for p in pdf.pages))

    def test_formatos_y_bloques(self):
        self.assertEqual(contratos.con_guion("1721129789"), "172112978-9")
        self.assertEqual(contratos.plazo_en_meses(date(2026, 11, 9), date(2027, 2, 8)), "3")
        self.assertEqual(contratos.plazo_en_meses(date(2026, 11, 9), None), "")
        self.assertEqual(contratos.dinero_corto(Decimal("1300")), "$1.300")
        self.assertEqual(contratos.dinero_en_letras_texto(Decimal("300.50")), "trescientos dólares con 50/100 de los Estados Unidos de América")
        texto = "# A\n# B\n\n## Cláusula\n\n1.1 Texto\n\n| a | b |\n| 1 | 2 |\n\n[[espacio]]\n\nFin"
        tipos = [t for t, _ in contratos.bloques(texto)]
        self.assertEqual(tipos, ["titulo", "clausula", "numeral", "tabla", "espacio", "parrafo"])

