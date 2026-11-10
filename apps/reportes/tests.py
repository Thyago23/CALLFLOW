"""4.5 Reportes y exportación: SCRUM-61 a SCRUM-69."""
import io
from datetime import date
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from apps.cierre.models import MetaMensual
from apps.ventas.models import FormularioDAMI
from apps.ventas.tests import BaseVentas

from . import estadisticas as stats
from .catalogo import ESTADISTICAS, REPORTES, ventas_por_vendedor
from .models import ReporteGenerado


def texto_pdf(respuesta) -> str:
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(respuesta.content)).pages)


class BaseReportes(BaseVentas):
    def url(self, codigo, pdf=False):
        return reverse("reportes:reporte_pdf" if pdf else "reportes:reporte", args=[codigo.lower()])

    def ventas(self):
        """Lucía: 3 DAMI (uno cerrado por 150,50, uno no concretado y uno pendiente). Pedro: 1 cerrado por 100."""
        self.crear(enviar=True)
        self.crear(enviar=True, cedula="0926687856")
        self.crear(enviar=True, cedula="1710000009")
        self.crear(self.pedro, enviar=True, cedula="1714616123")
        f1, f2, _, f4 = FormularioDAMI.objects.order_by("pk")
        self.cerrar(f1, valor=D("150.50"))
        self.cerrar(f2, resultado="NO_CONCRETADA")
        self.cerrar(f4, valor=D("100"))


class CentroTests(BaseReportes):
    """SCRUM-62: cada rol ve solo sus reportes."""

    def test_el_administrador_ve_todos_y_las_estadisticas(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("reportes:centro"))
        for r in REPORTES.values():
            self.assertContains(respuesta, r.nombre)
        self.assertContains(respuesta, reverse("reportes:estadisticas"))

    def test_el_encargado_solo_ve_los_comerciales(self):
        self.client.force_login(self.ana)
        respuesta = self.client.get(reverse("reportes:centro"))
        for codigo in ("VENTAS_POR_VENDEDOR", "VENTAS_FORMULARIOS", "VENTAS_PRODUCCION", "CIERRE_PANEL"):
            self.assertContains(respuesta, REPORTES[codigo].nombre)
        for codigo in ("PERSONAL_LISTADO", "NOMINA_RESUMEN", "NOMINA_COMISIONES", "ASISTENCIA_HISTORIAL"):
            self.assertNotContains(respuesta, REPORTES[codigo].nombre)
        self.assertNotContains(respuesta, reverse("reportes:estadisticas"))
        for url in (self.url("PERSONAL_LISTADO"), self.url("NOMINA_RESUMEN", pdf=True), reverse("reportes:estadisticas"),
                    reverse("reportes:estadisticas_pdf")):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertFalse(ReporteGenerado.objects.exists())

    def test_el_vendedor_no_entra_a_reportes(self):
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.get(reverse("reportes:centro")).status_code, 403)
        self.assertEqual(self.client.get(self.url("VENTAS_PRODUCCION", pdf=True), {"vendedor": self.pedro.empleado.pk}).status_code, 403)

    def test_reporte_que_no_existe(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("NO_EXISTE")).status_code, 404)


class ReportesTests(BaseReportes):
    """SCRUM-63 a SCRUM-66."""

    def test_todos_se_ven_y_se_descargan_en_pdf_y_quedan_registrados(self):
        self.ventas()
        self.client.force_login(self.admin)
        extra = {"NOMINA_ROL_PAGO": {"empleado": self.lucia.empleado.pk}, "VENTAS_PRODUCCION": {"vendedor": self.lucia.empleado.pk}}
        for codigo, r in REPORTES.items():
            with self.subTest(codigo=codigo):
                params = extra.get(codigo, {})
                vista = self.client.get(self.url(codigo), params)
                self.assertEqual(vista.status_code, 200)
                self.assertIsNotNone(vista.context["tabla"])
                pdf = self.client.get(self.url(codigo, pdf=True), params)
                self.assertEqual(pdf["Content-Type"], "application/pdf")
                self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(ReporteGenerado.objects.filter(generado_por=self.admin).count(), len(REPORTES))
        registro = ReporteGenerado.objects.get(tipo__codigo="VENTAS_PRODUCCION")
        self.assertEqual(registro.filtros["vendedor"]["id"], self.lucia.empleado.pk)
        self.assertGreater(registro.tamano_bytes, 0)

    def test_ventas_por_vendedor_calcula_ticket_y_tasa(self):
        self.ventas()
        hoy = timezone.localdate()
        lucia, pedro = ventas_por_vendedor(hoy, hoy)
        self.assertEqual(lucia["vendedor"], self.lucia.empleado)  # ordenado por total vendido
        self.assertEqual((lucia["enviados"], lucia["cerradas"], lucia["no_concretadas"], lucia["pendientes"]), (3, 1, 1, 1))
        self.assertEqual((lucia["total"], lucia["ticket"], lucia["tasa"]), (D("150.50"), D("150.50"), D("50")))
        self.assertEqual(pedro["total"], D("100.00"))
        self.client.force_login(self.ana)
        texto = texto_pdf(self.client.get(self.url("VENTAS_POR_VENDEDOR", pdf=True)))
        for esperado in ("VENTAS POR VENDEDOR", "Mora Lucía", "$150,50", "$250,50", "50 %", "Página 1 de 1"):
            self.assertIn(esperado, texto)

    def test_filtro_de_vendedor(self):
        self.ventas()
        self.client.force_login(self.admin)
        tabla = self.client.get(self.url("VENTAS_POR_VENDEDOR"), {"vendedor": self.pedro.empleado.pk}).context["tabla"]
        self.assertEqual([f[0] for f in tabla.filas], ["Paz Pedro"])

    def test_formularios_sin_datos_de_tarjeta(self):
        self.ventas()
        self.client.force_login(self.admin)
        vista = self.client.get(self.url("VENTAS_FORMULARIOS"), {"estado": "VENTA_CERRADA"})
        self.assertEqual(len(vista.context["tabla"].filas), 2)
        self.assertNotContains(vista, "4111")
        self.assertNotIn("4111", texto_pdf(self.client.get(self.url("VENTAS_FORMULARIOS", pdf=True))))

    def test_rol_de_pagos_pide_el_empleado(self):
        self.client.force_login(self.admin)
        vista = self.client.get(self.url("NOMINA_ROL_PAGO"))
        self.assertIsNone(vista.context["tabla"])
        self.assertContains(vista, "Elija empleado")
        con_empleado = self.client.get(self.url("NOMINA_ROL_PAGO"), {"empleado": self.lucia.empleado.pk})
        self.assertContains(con_empleado, "Neto a recibir")
        self.assertIn("ROL DE PAGOS", texto_pdf(self.client.get(self.url("NOMINA_ROL_PAGO", pdf=True),
                                                                {"empleado": self.lucia.empleado.pk})))

    def test_filtros_invalidos_no_descargan(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(self.url("NOMINA_RESUMEN", pdf=True), {"mes": "2026-13"})
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(ReporteGenerado.objects.exists())

    def test_nomina_cuadra_con_el_modulo_de_nomina(self):
        from apps.nomina import calculo

        self.client.force_login(self.admin)
        tabla = self.client.get(self.url("NOMINA_RESUMEN")).context["tabla"]
        hoy = timezone.localdate()
        self.assertEqual(tabla.totales[-1], calculo.nomina_del_mes(hoy.year, hoy.month).total_neto)


class EstadisticasTests(BaseReportes):
    """SCRUM-67 a SCRUM-69."""

    def test_tendencia_lineal(self):
        self.assertEqual(stats.tendencia_lineal([100, 200, 300], 3), [D("400"), D("500"), D("600")])
        self.assertEqual(stats.tendencia_lineal([100, 200, 300], 2, desde=4), [D("500"), D("600")])  # saltando el mes en curso
        self.assertEqual(stats.tendencia_lineal([300, 200, 100], 2), [D("0"), D("0")])  # nunca negativa
        self.assertEqual(stats.tendencia_lineal([250], 2), [D("250"), D("250")])
        self.assertEqual(stats.tendencia_lineal([], 1), [D("0")])

    def test_indicadores_ranking_y_proyeccion_del_mes(self):
        self.ventas()
        hoy = timezone.localdate()
        MetaMensual.objects.create(vendedor=self.lucia.empleado, anio=hoy.year, mes=hoy.month, monto_meta=D("1000"))
        est = stats.calcular(hoy.year, hoy.month)
        a = est.actual
        self.assertEqual((a.total, a.cerradas, a.enviados, a.no_concretadas), (D("250.50"), 2, 4, 1))
        self.assertEqual(est.ranking[0]["vendedor"], self.lucia.empleado)
        self.assertEqual(est.meta, D("1000"))
        self.assertEqual(est.embudo, [("DAMI enviados", 4), ("Gestionados por cierre", 3), ("Ventas cerradas", 2)])
        self.assertTrue(est.en_curso)
        self.assertEqual(len(est.proximos), 3)
        if est.transcurridos:
            esperado = (D("250.50") * est.laborables / est.transcurridos).quantize(D("0.01"))
            self.assertEqual(est.proyeccion_mes, esperado)
        self.assertFalse(est.confiable)  # sin meses completos de historia

    def test_filtro_por_vendedor(self):
        self.ventas()
        hoy = timezone.localdate()
        est = stats.calcular(hoy.year, hoy.month, vendedor=self.pedro.empleado)
        self.assertEqual((est.actual.total, len(est.ranking)), (D("100.00"), 1))

    def test_un_mes_pasado_no_se_proyecta(self):
        anio, mes = stats.sumar_meses(timezone.localdate().year, timezone.localdate().month, -2)
        est = stats.calcular(anio, mes)
        self.assertFalse(est.en_curso)
        self.assertIsNone(est.proyeccion_mes)
        self.assertEqual(est.proximos, [])

    def test_pagina_y_pdf(self):
        self.ventas()
        self.client.force_login(self.admin)
        pagina = self.client.get(reverse("reportes:estadisticas"))
        for texto in ("Total vendido", "Cierre proyectado del mes", "<svg", "Ranking de vendedores", "Embudo del mes", "Ver datos"):
            self.assertContains(pagina, texto)
        pdf = self.client.get(reverse("reportes:estadisticas_pdf"))
        texto = texto_pdf(pdf)
        for esperado in ("ESTADÍSTICAS Y PROYECCIÓN DE VENTAS", "$250,50", "Proyección", "Ranking de vendedores"):
            self.assertIn(esperado, texto)
        self.assertTrue(ReporteGenerado.objects.filter(tipo__codigo=ESTADISTICAS.codigo).exists())

    def test_utilidades_de_fechas(self):
        self.assertEqual(stats.sumar_meses(2026, 11, 3), (2027, 2))
        self.assertEqual(stats.sumar_meses(2026, 1, -1), (2025, 12))
        self.assertEqual(stats.ultimo_dia(2028, 2), date(2028, 2, 29))
        self.assertEqual(stats.dias_laborables(date(2026, 11, 2), date(2026, 11, 8)), 5)  # lunes a domingo
