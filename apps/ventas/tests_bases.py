"""Bases comerciales desde Excel, reparto, categorización y seguimiento: SCRUM-93 a SCRUM-97."""
import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from apps.core.models import Notificacion

from . import bases_excel, servicios
from .models import BaseComercial, ContactoRepetido, FormularioDAMI, RegistroBase
from .tests import CLIENTE, BaseVentas

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


def csv_simple(filas: int = 5, desde: int = 0) -> SimpleUploadedFile:
    lineas = ["Ciudad;Categoria Tarjeta;Direccion;Nombre y Apellido;Cedula;Numero"]
    lineas += [f"Quito;Visa;Calle {i};Cliente Número {i};;09912345{i:02d}" for i in range(desde, desde + filas)]
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

    def test_una_fila_de_datos_con_palabras_de_encabezado_no_es_encabezado(self):
        """Hallado al probar: «sin número | CLIENTE SIN TELEFONO» en medio de los datos se tomaba como encabezados."""
        filas = [["0991234567", "PEREZ MARIA", "1710034065", "QUITO", "AV. AMAZONAS", "VISA ORO"],
                 ["0987654321", "VEGA CARLA", "0926687856", "GUAYAQUIL", "CDLA. KENNEDY", "DINERS"],
                 ["sin número", "CLIENTE SIN TELEFONO", "1710000009", "QUITO", "AV. COLON", "VISA ORO"],
                 ["0981112233", "TORRES LUIS", "1714616123", "CUENCA", "CALLE LARGA", "MASTERCARD"]]
        r = bases_excel.formatear(excel_sin_encabezados(filas))
        self.assertEqual((r.fila_encabezado, r.importadas, r.sin_telefono), (0, 3, 1))
        self.assertEqual(r.columnas["cedula"], "Columna C")

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
        otra = servicios.importar_base("CSV 2", "", csv_simple(6, desde=10), self.ana)
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

    def test_en_por_gestionar_el_contacto_marcado_sale_de_la_lista_y_se_actualizan_los_contadores(self):
        """Antes se quedaba en «Por gestionar» hasta refrescar la página."""
        self.client.force_login(self.lucia)
        url = reverse("ventas:contacto_categorizar", args=[self.de_lucia[0].pk])
        lista = self.client.get(reverse("ventas:mis_contactos"))  # por defecto: «Por gestionar»
        self.assertContains(lista, 'name="filtro" value="PENDIENTE"')
        respuesta = self.client.post(url, {"estado": "NO_CONTESTO", "filtro": "PENDIENTE"}, HTTP_HX_REQUEST="true")
        self.assertContains(respuesta, "contacto-hecho")  # aviso breve en lugar de la fila
        self.assertContains(respuesta, 'value="PENDIENTE" class="btn btn-link btn-sm p-0">Deshacer')
        self.assertContains(respuesta, 'id="resumen-contactos" hx-swap-oob="true"')  # contadores al día
        self.assertEqual(respuesta.context[-1]["totales"]["no_contesto"], 1)
        # «Deshacer» lo devuelve a la lista como fila completa.
        deshacer = self.client.post(url, {"estado": "PENDIENTE", "filtro": "PENDIENTE"}, HTTP_HX_REQUEST="true")
        self.assertContains(deshacer, 'class="contacto-base estado-pendiente')
        # Con «Todos» la fila se queda, actualizada.
        todos = self.client.post(url, {"estado": "NO_QUIERE", "filtro": ""}, HTTP_HX_REQUEST="true")
        self.assertContains(todos, 'class="contacto-base estado-no_quiere')
        self.assertNotContains(todos, "contacto-hecho")

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


class RepetidosTests(BaseVentas):
    """Contactos repetidos: van a la base de repetidos y se avisa a administradores y encargados."""

    def csv(self, *filas, nombre="base.csv"):
        lineas = ["Nombre y Apellido;Cedula;Numero;Celular"] + [";".join(f) for f in filas]
        return SimpleUploadedFile(nombre, "\n".join(lineas).encode("utf-8"), content_type="text/csv")

    def test_repetidos_dentro_del_mismo_archivo(self):
        base = servicios.importar_base("A", "", self.csv(
            ["Maria Perez", "0926687856", "0991111111", ""],
            ["Maria P.", "0926687856", "0992222222", ""],  # misma cédula
            ["Carla Vega", "", "0993333333", "0991111111"],  # su celular es el número de María
            ["Luis Torres", "", "0994444444", ""]), self.ana)
        self.assertEqual(base.registros.count(), 2)
        repetidos = {r.nombre: r for r in base.repetidos.select_related("original")}
        self.assertEqual(set(repetidos), {"Maria P.", "Carla Vega"})
        self.assertEqual((repetidos["Maria P."].criterio, repetidos["Carla Vega"].criterio), ("CEDULA", "NUMERO"))
        self.assertTrue(all(r.motivo == "MISMO_ARCHIVO" and r.original.nombre == "Maria Perez" for r in repetidos.values()))
        self.assertEqual(base.resumen_carga["duplicadas"], 2)

    def test_repetidos_de_otra_base_no_entran_y_se_avisa(self):
        primera = servicios.importar_base("Primera", "", self.csv(["Maria Perez", "0926687856", "0991111111", "0995555555"],
                                                                  ["Luis Torres", "", "0994444444", ""]), self.ana)
        servicios.asignar_base(primera, [self.lucia.empleado], self.ana)
        Notificacion.objects.all().delete()
        segunda = servicios.importar_base("Segunda", "", self.csv(
            ["Maria Perez", "0926687856", "0990000001", ""],  # misma cédula
            ["Otra Persona", "", "0995555555", ""],  # era el «otro número» de María
            ["Nueva Persona", "", "0996666666", ""]), self.ana)
        self.assertEqual(list(segunda.registros.values_list("nombre", flat=True)), ["Nueva Persona"])
        self.assertEqual((segunda.total_registros, segunda.resumen_carga["repetidos_otras_bases"]), (1, 2))
        maria = segunda.repetidos.get(nombre="Maria Perez")
        self.assertEqual((maria.motivo, maria.criterio, maria.original.base, maria.original.vendedor),
                         ("OTRA_BASE", "CEDULA", primera, self.lucia.empleado))
        # Aviso a administradores y encargados de cierre; los vendedores no.
        avisados = set(Notificacion.objects.filter(tipo="CONTACTOS_REPETIDOS").values_list("destinatario__username", flat=True))
        self.assertEqual(avisados, {"ana.torres", "diego.mendoza"})
        aviso = Notificacion.objects.filter(destinatario=self.ana).get()
        self.assertIn("2 contactos repetidos", aviso.titulo)
        self.assertIn(f"base={segunda.pk}", aviso.url)
        # Al repartir la segunda base, los repetidos no se envían.
        servicios.asignar_base(segunda, [self.pedro.empleado], self.ana)
        self.assertEqual(RegistroBase.objects.filter(vendedor=self.pedro.empleado).count(), 1)

    def test_aviso_en_pantalla_al_cargar(self):
        servicios.importar_base("Primera", "", csv_simple(3), self.ana)
        self.client.force_login(self.ana)
        respuesta = self.client.post(reverse("ventas:bases"), {"cargar": "", "c-nombre": "Segunda", "c-archivo": csv_simple(5)},
                                     follow=True)
        self.assertContains(respuesta, "Se encontraron 3 contactos repetidos")
        self.assertContains(respuesta, "3 contactos repetidos por revisar")

    def test_revisar_descartar_y_recuperar(self):
        servicios.importar_base("Primera", "", csv_simple(2), self.ana)
        segunda = servicios.importar_base("Segunda", "", csv_simple(3), self.ana)
        uno, dos = segunda.repetidos.order_by("fila")
        self.client.force_login(self.ana)
        lista = self.client.get(reverse("ventas:repetidos"))
        self.assertContains(lista, uno.numero)
        self.client.post(reverse("ventas:repetido_revisar", args=[uno.pk]), {"accion": "descartar"})
        self.client.post(reverse("ventas:repetido_revisar", args=[dos.pk]), {"accion": "recuperar"})
        for objeto in (uno, dos, segunda):
            objeto.refresh_from_db()
        self.assertEqual((uno.estado, dos.estado, dos.revisado_por), ("DESCARTADO", "RECUPERADO", self.ana))
        self.assertTrue(segunda.registros.filter(numero=dos.numero, vendedor__isnull=True).exists())
        self.assertEqual(segunda.total_registros, 2)
        # Lo recuperado no vuelve a marcarse como repetido al buscar de nuevo.
        self.assertEqual(servicios.buscar_repetidos()["movidos"], 0)
        # El vendedor no entra.
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.get(reverse("ventas:repetidos")).status_code, 403)
        self.assertEqual(self.client.post(reverse("ventas:repetido_revisar", args=[uno.pk]), {"accion": "recuperar"}).status_code, 403)

    def test_buscar_repetidos_en_bases_ya_cargadas(self):
        """Bases cargadas antes de esta función: se mueven los libres; los ya enviados se quedan."""
        vieja = BaseComercial.objects.create(nombre="Vieja", total_registros=2)
        nueva = BaseComercial.objects.create(nombre="Nueva", total_registros=2)
        RegistroBase.objects.create(base=vieja, fila=1, nombre="Maria", cedula="0926687856", numero="0991111111")
        RegistroBase.objects.create(base=vieja, fila=2, nombre="Luis", numero="0994444444")
        RegistroBase.objects.create(base=nueva, fila=1, nombre="Maria otra vez", cedula="0926687856", numero="0990000001")
        enviado = RegistroBase.objects.create(base=nueva, fila=2, nombre="Luis enviado", numero="0994444444",
                                              vendedor=self.lucia.empleado)
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("ventas:repetidos"), {"buscar": ""}, follow=True)
        self.assertContains(respuesta, "Se encontraron 1 contactos repetidos")
        self.assertContains(respuesta, "1 repetidos ya estaban enviados")
        self.assertFalse(RegistroBase.objects.filter(nombre="Maria otra vez").exists())
        self.assertTrue(RegistroBase.objects.filter(pk=enviado.pk).exists())
        repetido = ContactoRepetido.objects.get()
        self.assertEqual((repetido.base, repetido.motivo, repetido.original.nombre), (nueva, "OTRA_BASE", "Maria"))
        nueva.refresh_from_db()
        self.assertEqual(nueva.total_registros, 1)
        self.assertTrue(Notificacion.objects.filter(destinatario=self.ana, tipo="CONTACTOS_REPETIDOS").exists())
        self.assertEqual(servicios.buscar_repetidos()["movidos"], 0)  # una segunda vez no encuentra nada nuevo

    def test_descartar_todos_y_excel(self):
        servicios.importar_base("Primera", "", csv_simple(2), self.ana)
        segunda = servicios.importar_base("Segunda", "", csv_simple(3), self.ana)
        self.client.force_login(self.ana)
        archivo = self.client.get(reverse("ventas:repetidos"), {"excel": "1", "base": segunda.pk})
        hoja = load_workbook(io.BytesIO(archivo.content)).active
        self.assertEqual(hoja.max_row, 3)
        self.assertEqual(hoja.cell(1, 9).value, "MOTIVO")
        self.assertEqual(hoja["F2"].value, "0991234500")
        self.client.post(reverse("ventas:repetidos"), {"descartar_todos": "", "base": segunda.pk})
        self.assertEqual(servicios.repetidos_por_revisar(), 0)


class AyudaEntreAsesoresTests(BaseVentas):
    """Un vendedor pide ayuda a otro asesor con un contacto; el DAMI queda a nombre de quien pidió la ayuda."""

    def setUp(self):
        from .models import SolicitudAyuda

        self.SolicitudAyuda = SolicitudAyuda
        base = servicios.importar_base("CSV", "", csv_simple(2), self.ana)
        servicios.asignar_base(base, [self.lucia.empleado], self.ana)
        self.contacto = RegistroBase.objects.filter(vendedor=self.lucia.empleado).order_by("fila").first()
        Notificacion.objects.all().delete()

    def pedir(self, ayudante=None, usuario=None):
        self.client.force_login(usuario or self.lucia)
        return self.client.post(reverse("ventas:pedir_ayuda", args=[self.contacto.pk]),
                                {"ayudante": (ayudante or self.pedro).empleado.pk, "mensaje": "Llámalo en la tarde"}, follow=True)

    def responder(self, accion, usuario=None):
        s = self.SolicitudAyuda.objects.get(contacto=self.contacto, estado__in=self.SolicitudAyuda.ACTIVAS)
        self.client.force_login(usuario or self.pedro)
        return self.client.post(reverse("ventas:responder_ayuda", args=[s.pk]), {"accion": accion}, follow=True)

    def test_pedir_aceptar_y_el_dami_queda_a_nombre_de_quien_pidio(self):
        respuesta = self.pedir()
        self.assertContains(respuesta, "Ayuda pedida a")
        aviso = Notificacion.objects.get(destinatario=self.pedro)
        self.assertIn("Lucía Mora te pide ayuda", aviso.titulo)
        # Pedro ve la petición y la acepta; Lucía recibe la respuesta.
        self.client.force_login(self.pedro)
        self.assertContains(self.client.get(reverse("ventas:mis_contactos")), "Te pidieron ayuda")
        respuesta = self.responder("aceptar")
        self.assertContains(respuesta, "Cliente de Lucía Mora")
        self.assertIn("aceptó ayudarte", Notificacion.objects.get(destinatario=self.lucia).titulo)
        # Pedro gestiona el contacto (sigue siendo de Lucía: cuenta en su seguimiento).
        self.client.post(reverse("ventas:contacto_categorizar", args=[self.contacto.pk]), {"estado": "DAMI_SACADO"})
        self.contacto.refresh_from_db()
        self.assertEqual((self.contacto.estado, self.contacto.vendedor), ("DAMI_SACADO", self.lucia.empleado))
        # El DAMI se prellena con el contacto y se envía a nombre de Lucía.
        nuevo = self.client.get(reverse("ventas:formulario_nuevo"), {"contacto": self.contacto.pk})
        self.assertEqual(nuevo.context["form"].initial["telefono"], self.contacto.numero)
        self.assertContains(nuevo, "como ayuda")
        self.assertContains(nuevo, "Lucía Mora")
        self.client.post(reverse("ventas:formulario_nuevo"), CLIENTE | {"contacto": self.contacto.pk, "enviar": ""})
        dami = FormularioDAMI.objects.get()
        self.assertEqual((dami.vendedor, dami.ayudado_por, dami.estado), (self.lucia.empleado, self.pedro.empleado, "ENVIADO"))
        self.assertEqual(dami.registro_produccion.hoja.vendedor, self.lucia.empleado)  # cuenta en la hoja de Lucía

    def test_sin_aceptar_no_gestiona_ni_saca_el_dami_a_nombre_del_otro(self):
        self.pedir()
        self.client.force_login(self.pedro)
        self.assertNotContains(self.client.get(reverse("ventas:mis_contactos"), {"estado": ""}), "Cliente de Lucía Mora")
        marcar = self.client.post(reverse("ventas:contacto_categorizar", args=[self.contacto.pk]), {"estado": "NO_QUIERE"})
        self.assertEqual(marcar.status_code, 403)
        self.client.post(reverse("ventas:formulario_nuevo"), CLIENTE | {"contacto": self.contacto.pk, "enviar": ""})
        self.assertEqual(FormularioDAMI.objects.get().vendedor, self.pedro.empleado)  # sin ayuda aceptada, es un DAMI propio

    def test_rechazar_cancelar_y_reglas(self):
        self.pedir()
        self.assertContains(self.pedir(), "ya tiene una ayuda pedida")  # una sola ayuda en curso por contacto
        self.responder("rechazar")
        self.assertIn("no puede ayudarte", Notificacion.objects.get(destinatario=self.lucia).titulo)
        # Puede pedírsela a otro; no a sí mismo ni con un contacto ajeno.
        self.client.force_login(self.lucia)
        a_si_mismo = self.client.post(reverse("ventas:pedir_ayuda", args=[self.contacto.pk]), {"ayudante": self.lucia.empleado.pk}, follow=True)
        self.assertContains(a_si_mismo, "Elija el asesor")
        self.client.force_login(self.pedro)
        ajeno = self.client.post(reverse("ventas:pedir_ayuda", args=[self.contacto.pk]), {"ayudante": self.lucia.empleado.pk})
        self.assertEqual(ajeno.status_code, 403)
        # Retirar una ayuda ya aceptada: el ayudante deja de ver el contacto.
        self.pedir()
        self.responder("aceptar")
        s = self.SolicitudAyuda.objects.get(estado="ACEPTADA")
        self.client.force_login(self.lucia)
        self.client.post(reverse("ventas:cancelar_ayuda", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.estado, "CANCELADA")
        self.client.force_login(self.pedro)
        self.assertNotContains(self.client.get(reverse("ventas:mis_contactos"), {"estado": ""}), "Cliente de Lucía Mora")
        self.assertEqual(self.client.post(reverse("ventas:responder_ayuda", args=[s.pk]), {"accion": "aceptar"}, follow=True).status_code, 200)
        s.refresh_from_db()
        self.assertEqual(s.estado, "CANCELADA")  # ya no se puede aceptar
