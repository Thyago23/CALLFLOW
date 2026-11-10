"""Bases comerciales desde Excel, reparto, categorización y seguimiento: SCRUM-93 a SCRUM-97."""
import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from apps.core.models import Notificacion

from . import bases_excel, servicios
from .models import BaseComercial, RegistroBase
from .tests import BaseVentas

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def excel_desordenado() -> SimpleUploadedFile:
    """Como llegan de los proveedores: títulos arriba, otros nombres y orden, números como en Excel."""
    libro = Workbook()
    hoja = libro.active
    hoja.append(["BASE CLIENTES BANCO X - NOVIEMBRE"])
    hoja.append([])
    hoja.append(["N°", "APELLIDOS", "NOMBRES", "C.I.", "Teléfono", "Celular", "Dirección domicilio", "Ciudad", "Tarjeta"])
    hoja.append([1, "PÉREZ GÓMEZ", "MARÍA JOSÉ", 926687856, 22345678, "593991234567", "AV. AMAZONAS  N34-12", "QUITO", "VISA ORO"])
    hoja.append([2, "vega", "carla", "1710034065", None, 991234568.0, "calle 10 y av. 9", "guayaquil", "mastercard"])
    hoja.append([3, "SIN", "TELEFONO", "1710000009", "abc", None, "", "Cuenca", "Visa"])
    hoja.append([])
    hoja.append([4, "REPETIDA", "MARÍA", "0926687856", "0987654321", None, "", "Quito", "Visa"])
    hoja.append([5, "CEDULA", "MALA", "1234567890", "0981112233", None, "", "Ambato", "Diners"])
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile("base banco x.xlsx", salida.getvalue(), content_type=XLSX)


def excel_sin_encabezados(filas) -> SimpleUploadedFile:
    libro = Workbook()
    for fila in filas:
        libro.active.append(fila)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile("sin encabezados.xlsx", salida.getvalue(), content_type=XLSX)


def csv_simple(filas: int = 5) -> SimpleUploadedFile:
    lineas = ["Ciudad;Categoria Tarjeta;Direccion;Nombre y Apellido;Cedula;Numero"]
    lineas += [f"Quito;Visa;Calle {i};Cliente Número {i};;09912345{i:02d}" for i in range(filas)]
    return SimpleUploadedFile("base.csv", "\n".join(lineas).encode("latin-1"), content_type="text/csv")


class FormatoExcelTests(BaseVentas):
    def test_reconoce_columnas_y_limpia_los_datos(self):
        r = bases_excel.formatear(excel_desordenado())
        self.assertEqual(r.fila_encabezado, 3)
        self.assertEqual((r.leidas, r.importadas, r.sin_telefono, r.duplicadas, r.cedulas_invalidas), (5, 3, 1, 1, 1))
        maria, carla, mala = r.filas
        self.assertEqual(maria["nombre"], "María José Pérez Gómez")  # nombres + apellidos, con mayúsculas iniciales
        self.assertEqual((maria["cedula"], maria["cedula_valida"]), ("0926687856", True))  # Excel le quitó el 0
        self.assertEqual((maria["numero"], maria["numero_alterno"]), ("022345678", "0991234567"))  # 593… → 0…
        self.assertEqual((maria["ciudad"], maria["direccion"], maria["categoria_tarjeta"]), ("Quito", "Av. Amazonas N34-12", "Visa Oro"))
        self.assertEqual((carla["numero"], carla["nombre"], carla["ciudad"]), ("0991234568", "Carla Vega", "Guayaquil"))
        self.assertFalse(mala["cedula_valida"])
        self.assertEqual(r.columnas["cedula"], "C.I.")

    def test_csv_con_punto_y_coma_y_latin1(self):
        r = bases_excel.formatear(csv_simple(3))
        self.assertEqual(r.importadas, 3)
        self.assertEqual(r.filas[0]["nombre"], "Cliente Número 0")

    def test_sin_encabezados_y_en_desorden_reconoce_por_el_contenido(self):
        """Como llegan de verdad: sin títulos ni encabezados y cada proveedor en su orden."""
        filas = [  # número, nombre, cédula, ciudad, dirección, tarjeta
            [991234567, "PEREZ GOMEZ MARIA JOSE", 926687856, "QUITO", "AV. AMAZONAS N34-12", "VISA ORO"],
            ["0987654321", "VEGA CARLA", "1710034065", "GUAYAQUIL", "CDLA. KENNEDY MZ 4 VILLA 2", "MASTERCARD PLATINUM"],
            ["593981112233", "TORRES LUIS ALBERTO", "1710000009", "CUENCA", "CALLE LARGA 5-12", "DINERS"],
            ["022345678", "MORA ANA", "1804567890", "AMBATO", "BARRIO FICOA", "VISA CLASICA"],
        ]
        r = bases_excel.formatear(excel_sin_encabezados(filas))
        self.assertEqual(r.fila_encabezado, 0)
        self.assertEqual(r.importadas, 4)  # ninguna fila se tomó como encabezado
        self.assertEqual(r.columnas, {"numero": "Columna A", "nombre": "Columna B", "cedula": "Columna C", "ciudad": "Columna D",
                                      "direccion": "Columna E", "categoria_tarjeta": "Columna F"})
        primera = r.filas[0]
        self.assertEqual((primera["numero"], primera["cedula"], primera["nombre"], primera["ciudad"], primera["categoria_tarjeta"]),
                         ("0991234567", "0926687856", "Perez Gomez Maria Jose", "Quito", "Visa Oro"))
        self.assertEqual(r.filas[2]["numero"], "0981112233")

    def test_sin_encabezados_en_el_orden_del_formato(self):
        """Ciudades poco comunes y sin cédula: lo que no se deduce se toma del orden de la empresa."""
        filas = [["Pimampiro", "Visa", "Sector El Inca", "Juan Pablo Ruiz", "", "0991112222"],
                 ["Pimampiro", "Visa", "Calle Bolívar 3", "Rosa Elena Paz", "", "0993334444"],
                 ["Mira", "Mastercard", "Vía a Ibarra km 2", "Pedro Luis Andrade", "", "0995556666"]]
        r = bases_excel.formatear(excel_sin_encabezados(filas))
        self.assertEqual(r.importadas, 3)
        self.assertEqual((r.filas[0]["ciudad"], r.filas[0]["nombre"], r.filas[0]["direccion"]),
                         ("Pimampiro", "Juan Pablo Ruiz", "Sector El Inca"))

    def test_sin_telefonos_no_se_puede_cargar(self):
        archivo = SimpleUploadedFile("x.csv", b"a;b;c\n1;2;3\n", content_type="text/csv")
        with self.assertRaisesMessage(ValidationError, "números de teléfono"):
            bases_excel.formatear(archivo)

    def test_formato_no_admitido(self):
        with self.assertRaises(ValidationError):
            bases_excel.formatear(SimpleUploadedFile("base.pdf", b"%PDF-1.4"))

    def test_telefonos(self):
        self.assertEqual(bases_excel.telefonos("0991234567 / 022345678", "+593 99 765 4321"),
                         ["0991234567", "022345678", "0997654321"])
        self.assertEqual(bases_excel.telefono("12345"), "")


class CargaYRepartoTests(BaseVentas):
    def cargar(self, usuario=None, archivo=None, nombre="Banco X noviembre"):
        self.client.force_login(usuario or self.ana)
        return self.client.post(reverse("ventas:bases"), {"cargar": "", "c-nombre": nombre, "c-archivo": archivo or excel_desordenado()})

    def test_el_encargado_carga_y_ve_el_resumen(self):
        respuesta = self.cargar()
        base = BaseComercial.objects.get(nombre="Banco X noviembre")
        self.assertRedirects(respuesta, reverse("ventas:base_detalle", args=[base.pk]))
        self.assertEqual((base.total_registros, base.registros.count(), base.cargada_por), (3, 3, self.ana))
        detalle = self.client.get(reverse("ventas:base_detalle", args=[base.pk]))
        for texto in ("Así se formateó el archivo", "C.I. → Cédula", "María José Pérez Gómez", "CATEGORIA TARJETA"):
            self.assertContains(detalle, texto)

    def test_el_administrador_tambien_carga_y_el_vendedor_no(self):
        self.assertEqual(self.cargar(self.admin).status_code, 302)
        self.assertTrue(BaseComercial.objects.filter(nombre="Banco X noviembre").exists())
        self.cargar(self.lucia, nombre="Del vendedor")
        self.assertFalse(BaseComercial.objects.filter(nombre="Del vendedor").exists())

    def test_archivo_malo_no_crea_la_base(self):
        respuesta = self.cargar(archivo=SimpleUploadedFile("x.csv", b"a;b\n1;2\n", content_type="text/csv"))
        self.assertContains(respuesta, "números de teléfono")
        self.assertFalse(BaseComercial.objects.exists())

    def test_reparte_en_partes_iguales_o_por_cantidad(self):
        base = servicios.importar_base("CSV", "", csv_simple(5), self.ana)
        self.client.force_login(self.ana)
        datos = {"asignar": "", "a-base": base.pk, "a-vendedores": [self.lucia.empleado.pk, self.pedro.empleado.pk]}
        respuesta = self.client.post(reverse("ventas:bases"), datos, follow=True)
        self.assertContains(respuesta, "Se enviaron 5 contactos")
        self.assertEqual(sorted(RegistroBase.objects.values_list("vendedor", flat=True)),
                         sorted([self.lucia.empleado.pk] * 3 + [self.pedro.empleado.pk] * 2))
        aviso = Notificacion.objects.get(destinatario=self.lucia)
        self.assertIn("3 contactos", aviso.titulo)
        otra = servicios.importar_base("CSV 2", "", csv_simple(6), self.ana)
        self.client.post(reverse("ventas:bases"), datos | {"a-base": otra.pk, "a-cantidad": "2"})
        self.assertEqual(otra.registros.filter(vendedor__isnull=False).count(), 4)
        self.assertEqual(otra.registros.filter(vendedor__isnull=True).count(), 2)

    def test_atender_una_solicitud_envia_los_contactos(self):
        base = servicios.importar_base("CSV", "", csv_simple(4), self.ana)
        solicitud = servicios.solicitar_base(self.lucia, "Terminé mi base")
        self.client.force_login(self.ana)
        self.client.post(reverse("ventas:responder_solicitud", args=[solicitud.pk]),
                         {"accion": "atender", "base": base.pk, "cantidad": "3", "respuesta": ""})
        self.assertEqual(RegistroBase.objects.filter(vendedor=self.lucia.empleado).count(), 3)

    def test_excel_con_el_formato_conserva_los_ceros(self):
        base = servicios.importar_base("X", "", excel_desordenado(), self.ana)
        self.client.force_login(self.ana)
        respuesta = self.client.get(reverse("ventas:base_excel", args=[base.pk]))
        hoja = load_workbook(io.BytesIO(respuesta.content)).active
        self.assertEqual([c.value for c in hoja[1]], bases_excel.ENCABEZADOS)
        self.assertEqual((hoja["E2"].value, hoja["F2"].value), ("0926687856", "022345678"))
        plantilla = load_workbook(io.BytesIO(self.client.get(reverse("ventas:bases_plantilla")).content)).active
        self.assertEqual(plantilla.max_row, 1)


class GestionDelVendedorTests(BaseVentas):
    def setUp(self):
        self.base = servicios.importar_base("CSV", "", csv_simple(4), self.ana)
        servicios.asignar_base(self.base, [self.lucia.empleado, self.pedro.empleado], self.ana)
        self.de_lucia = list(RegistroBase.objects.filter(vendedor=self.lucia.empleado).order_by("fila"))
        self.de_pedro = RegistroBase.objects.filter(vendedor=self.pedro.empleado).first()

    def marcar(self, registro, estado, usuario=None):
        self.client.force_login(usuario or self.lucia)
        return self.client.post(reverse("ventas:contacto_categorizar", args=[registro.pk]), {"estado": estado}, HTTP_HX_REQUEST="true")

    def test_ve_solo_sus_contactos_y_los_categoriza(self):
        self.client.force_login(self.lucia)
        lista = self.client.get(reverse("ventas:mis_contactos"))
        self.assertContains(lista, self.de_lucia[0].numero)
        self.assertNotContains(lista, self.de_pedro.numero)
        respuesta = self.marcar(self.de_lucia[0], "DAMI_SACADO")
        self.assertContains(respuesta, 'aria-pressed="true"')
        self.marcar(self.de_lucia[1], "NO_CONTESTO")
        self.marcar(self.de_lucia[1], "NO_QUIERE")  # lo corrige
        r = RegistroBase.objects.get(pk=self.de_lucia[1].pk)
        self.assertEqual((r.estado, r.intentos), ("NO_QUIERE", 2))
        self.marcar(self.de_lucia[0], "PENDIENTE")  # deshacer
        self.assertEqual(RegistroBase.objects.get(pk=self.de_lucia[0].pk).estado, "PENDIENTE")

    def test_no_toca_contactos_ajenos(self):
        self.assertEqual(self.marcar(self.de_pedro, "NO_QUIERE").status_code, 403)
        self.assertEqual(RegistroBase.objects.get(pk=self.de_pedro.pk).estado, "PENDIENTE")
        self.assertEqual(self.marcar(self.de_lucia[0], "OTRA_COSA").status_code, 400)

    def test_seguimiento_para_el_encargado(self):
        self.marcar(self.de_lucia[0], "DAMI_SACADO")
        self.marcar(self.de_lucia[1], "NO_CONTESTO")
        lucia = next(f for f in servicios.seguimiento() if f["vendedor"] == self.lucia.empleado)
        self.assertEqual((lucia["enviados"], lucia["gestionados"], lucia["pendientes"], lucia["dami_sacado"], lucia["no_contesto"],
                          lucia["avance"]), (2, 2, 0, 1, 1, 100))
        self.client.force_login(self.ana)
        pagina = self.client.get(reverse("ventas:bases_seguimiento"))
        self.assertContains(pagina, "Mora Lucía")
        self.assertContains(pagina, "Gestionó todos")  # Lucía no tiene faltantes
        detalle = self.client.get(reverse("ventas:bases_contactos"), {"vendedor": self.lucia.empleado.pk, "estado": "DAMI_SACADO"})
        self.assertEqual(len(detalle.context["pagina"].object_list), 1)
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.get(reverse("ventas:bases_seguimiento")).status_code, 403)

    def test_efectividad_de_la_base(self):
        self.marcar(self.de_lucia[0], "DAMI_SACADO")
        self.marcar(self.de_lucia[1], "NO_QUIERE")
        e = servicios.efectividad_bases(self.base)[0]
        self.assertEqual((e["contactos"], e["gestionados"], e["dami_sacado"], e["efectividad"], e["efectividad_total"]), (4, 2, 1, 50, 25))

    def test_llenar_dami_desde_un_contacto(self):
        self.client.force_login(self.lucia)
        propio = self.client.get(reverse("ventas:formulario_nuevo"), {"contacto": self.de_lucia[0].pk})
        self.assertEqual(propio.context["form"].initial["telefono"], self.de_lucia[0].numero)
        ajeno = self.client.get(reverse("ventas:formulario_nuevo"), {"contacto": self.de_pedro.pk})
        self.assertNotIn("telefono", ajeno.context["form"].initial)

    def test_la_base_exige_vendedor_para_categorizar(self):
        libre = RegistroBase.objects.create(base=self.base, numero="0990000000")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistroBase.objects.filter(pk=libre.pk).update(estado="NO_QUIERE")

    def test_reportes_de_bases(self):
        self.marcar(self.de_lucia[0], "DAMI_SACADO")
        self.client.force_login(self.ana)
        for codigo in ("bases_efectividad", "bases_gestion"):
            vista = self.client.get(reverse("reportes:reporte", args=[codigo]))
            self.assertEqual(len(vista.context["tabla"].filas), 1 if codigo == "bases_efectividad" else 2)
            pdf = self.client.get(reverse("reportes:reporte_pdf", args=[codigo]))
            self.assertTrue(pdf.content.startswith(b"%PDF"))
