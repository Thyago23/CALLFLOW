"""Validación de tarjetas: marcas, Luhn, caducidad, catálogo de BIN e importación."""
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from . import bin as bines
from . import tarjetas
from .models import EntidadEmisora, RangoBIN
from .tests import BaseVentas

# Números de prueba públicos de cada red (no son tarjetas reales).
PRUEBA = {
    "VISA": "4111111111111111", "MASTERCARD": "5555555555554444", "AMEX": "378282246310005",
    "DINERS": "36227206271667", "DISCOVER": "6011111111111117", "JCB": "3530111333300000",
    "UNIONPAY": "6200000000000005", "MAESTRO": "6759649826438453",
}
HOY = date(2026, 11, 6)


class MarcasTests(BaseVentas):
    def test_detecta_todas_las_marcas(self):
        for marca, numero in PRUEBA.items():
            with self.subTest(marca=marca):
                self.assertEqual(tarjetas.detectar_marca(numero), marca)
                self.assertTrue(bines.analizar(numero, hoy=HOY).valida)
        self.assertEqual(tarjetas.detectar_marca("2221000000000009"), "MASTERCARD")  # serie 2
        self.assertEqual(tarjetas.detectar_marca("6221260000000000"), "DISCOVER")  # gana el prefijo más largo
        self.assertIsNone(tarjetas.detectar_marca("9999"))

    def test_analisis_paso_a_paso(self):
        r = bines.analizar("4111 11", hoy=HOY)
        self.assertEqual((r.veredicto, r.marca), ("incompleta", "VISA"))
        r = bines.analizar("4111 1111 1111 1112", hoy=HOY)
        self.assertEqual(r.veredicto, "invalida")
        self.assertIn("mal escrito", " ".join(x.detalle for x in r.revisiones))
        r = bines.analizar("9999 9999 9999 9999", hoy=HOY)
        self.assertEqual(r.veredicto, "invalida")
        r = bines.analizar(PRUEBA["VISA"], "01/20", hoy=HOY)
        self.assertEqual(r.veredicto, "invalida")  # caducada
        self.assertEqual(bines.analizar(PRUEBA["VISA"], "1031", hoy=HOY).veredicto, "valida")

    def test_mascara_no_expone_el_numero(self):
        self.assertEqual(bines.enmascarar("4111111111111111"), "411111••••••1111")


class CatalogoBINTests(BaseVentas):
    def setUp(self):
        self.pichincha = EntidadEmisora.objects.get(nombre="Banco Pichincha")

    def test_catalogo_de_entidades_del_ecuador(self):
        for nombre in ("Banco Guayaquil", "Produbanco", "Banco Diners Club del Ecuador", "Cooperativa JEP", "Mutualista Pichincha"):
            self.assertTrue(EntidadEmisora.objects.filter(nombre=nombre).exists(), nombre)

    def test_identifica_el_banco_por_bin_mas_largo(self):
        otra = EntidadEmisora.objects.get(nombre="Produbanco")
        RangoBIN.objects.create(bin="411111", entidad=self.pichincha, marca="VISA")
        RangoBIN.objects.create(bin="41111111", entidad=otra, marca="VISA", tipo_tarjeta="DEBITO")
        r = bines.analizar(PRUEBA["VISA"], hoy=HOY)
        self.assertEqual((r.rango.entidad, r.rango.tipo_tarjeta), (otra, "DEBITO"))
        r = bines.analizar("4111119999999999", hoy=HOY)
        self.assertEqual(r.rango.entidad, self.pichincha)

    def test_sin_bin_es_aviso_no_error(self):
        r = bines.analizar(PRUEBA["MASTERCARD"], hoy=HOY)
        self.assertTrue(r.valida)
        self.assertIn("no está en el catálogo", " ".join(x.detalle for x in r.revisiones))

    def test_pantalla_y_resultado_htmx(self):
        self.client.force_login(self.lucia)
        self.assertContains(self.client.get(reverse("ventas:validar_tarjeta")), "Validar tarjeta")
        respuesta = self.client.post(reverse("ventas:validar_tarjeta"), {"numero": PRUEBA["AMEX"]}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "Número válido")
        self.assertContains(respuesta, "American Express")
        self.assertNotContains(respuesta, PRUEBA["AMEX"])  # nunca se devuelve el número completo

    def test_vendedor_consulta_pero_no_edita(self):
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.get(reverse("ventas:catalogo_bin")).status_code, 200)
        datos = {"b-bin": "411111", "b-entidad": self.pichincha.pk, "b-marca": "VISA", "b-tipo_tarjeta": "CREDITO", "agregar_bin": ""}
        self.assertEqual(self.client.post(reverse("ventas:catalogo_bin"), datos).status_code, 403)

    def test_encargado_agrega_y_valida_que_la_marca_coincida(self):
        self.client.force_login(self.ana)
        url = reverse("ventas:catalogo_bin")
        datos = {"b-bin": "411111", "b-entidad": self.pichincha.pk, "b-marca": "MASTERCARD", "b-tipo_tarjeta": "CREDITO", "agregar_bin": ""}
        self.assertContains(self.client.post(url, datos), "Ese BIN es de Visa")
        self.client.post(url, datos | {"b-marca": "VISA"})
        rango = RangoBIN.objects.get()
        self.assertEqual((rango.bin, rango.registrado_por), ("411111", self.ana))
        self.client.post(reverse("ventas:eliminar_bin", args=[rango.pk]))
        self.assertFalse(RangoBIN.objects.exists())

    def test_importar_csv(self):
        self.client.force_login(self.admin)
        contenido = ("bin;entidad;marca;tipo\n"
                     "411111;banco pichincha;Visa;crédito\n"
                     "555555;PRODUBANCO;Mastercard;debito\n"
                     "12;Banco Pichincha;Visa;credito\n"
                     "422222;Banco Inexistente;Visa;credito\n"
                     "433333;Banco Pichincha;Amex;credito\n").encode()
        archivo = SimpleUploadedFile("bins.csv", contenido, content_type="text/csv")
        respuesta = self.client.post(reverse("ventas:catalogo_bin"), {"i-archivo": archivo, "importar": ""}, follow=True)
        self.assertContains(respuesta, "2 BIN nuevos")
        self.assertEqual(set(RangoBIN.objects.values_list("bin", "tipo_tarjeta")), {("411111", "CREDITO"), ("555555", "DEBITO")})
        for error in ("6 a 8 dígitos", "no está en el catálogo", "no corresponde a American Express"):
            self.assertContains(respuesta, error)
