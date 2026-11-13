"""
SCRUM-14 · Salarios y comisiones, con las reglas de la empresa.

    python manage.py test apps.nomina
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.asistencia.models import AsignacionHorario, Horario, NovedadAsistencia
from apps.asistencia.servicios import minutos_atraso
from apps.cierre.models import GestionCierre
from apps.core.models import Rol, Usuario
from apps.documentos.models import Documento, TipoDocumento
from apps.personal.models import AsignacionComision, Cargo, Empleado, EsquemaComision, HistorialSalario, ObservacionEmpleado
from apps.ventas.models import Cliente, FormularioDAMI

from . import calculo

ZONA = timezone.get_default_timezone()
D = Decimal


class ComisionesTests(TestCase):
    """Tramos de la empresa: el porcentaje alcanzado se aplica a todo el total del mes."""

    def test_visas(self):
        visas = EsquemaComision.objects.get(nombre="Visado")
        casos = {"0": ("0", "0.00"), "1300": ("0", "0.00"), "1301": ("11", "143.11"), "3000": ("11", "330.00"),
                 "3001": ("15", "450.15"), "3500": ("15", "525.00")}
        for total, (pct, monto) in casos.items():
            with self.subTest(total=total):
                self.assertEqual(calculo.comision(visas, D(total)), (D(pct), D(monto)))

    def test_baymont_travel(self):
        baymont = EsquemaComision.objects.get(nombre="Paquetería")
        casos = {"3000": ("0", "0.00"), "3001": ("11", "330.11"), "5000": ("11", "550.00"), "5001": ("15", "750.15")}
        for total, (pct, monto) in casos.items():
            with self.subTest(total=total):
                self.assertEqual(calculo.comision(baymont, D(total)), (D(pct), D(monto)))

    def test_sueldo_base_por_defecto_es_400(self):
        self.assertEqual(calculo.sueldo_base_por_defecto(), D("400.00"))


class AtrasosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)

    def setUp(self):
        self.horario = Horario.objects.get(nombre="Jornada estándar")
        self.dia = date(2026, 11, 4)  # miércoles

    def minutos(self, h, m, s):
        return minutos_atraso(self.horario, self.dia, datetime.combine(self.dia, time(h, m, s), tzinfo=ZONA))

    def test_jornada_estandar_entra_a_las_nueve_sin_tolerancia(self):
        self.assertEqual((self.horario.hora_entrada, self.horario.minutos_tolerancia), (time(9, 0), 0))

    def test_cada_minuto_empezado_cuenta(self):
        casos = {(9, 0, 0): 0, (8, 59, 59): 0, (9, 0, 1): 1, (9, 1, 0): 1, (9, 1, 1): 2, (9, 10, 0): 10, (9, 10, 1): 11}
        for hora, esperado in casos.items():
            with self.subTest(hora=hora):
                self.assertEqual(self.minutos(*hora), esperado)

    def test_multas_por_tramo(self):
        reglas = calculo.parametros("PENALIZACIONES", date(2026, 11, 30))
        casos = {1: D("1.00"), 2: D("5.00"), 10: D("5.00"), 11: None, 45: None}
        for minutos, esperado in casos.items():
            with self.subTest(minutos=minutos):
                self.assertEqual(calculo.multa_por_atraso(minutos, reglas), esperado)


class EstimacionMesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("inicializar_datos", verbosity=0)
        hoy = timezone.localdate()
        cls.anio, cls.mes = hoy.year, hoy.month
        cls.inicio = date(cls.anio, cls.mes, 1)
        vendedor_cargo = Cargo.objects.get(nombre="Asesor de ventas")
        cls.vendedor = Empleado.objects.create(identificacion="1710034065", nombres="Ana", apellidos="Vera",
                                               cargo=vendedor_cargo, fecha_ingreso=date(2026, 1, 1))
        cls.encargado = Empleado.objects.create(identificacion="0926687856", nombres="Luis", apellidos="Paz",
                                                cargo=Cargo.objects.get(nombre="Supervisor"), fecha_ingreso=date(2026, 1, 1))
        HistorialSalario.objects.create(empleado=cls.vendedor, salario_base=D("400.00"), vigente_desde=date(2026, 1, 1))
        AsignacionComision.objects.create(empleado=cls.vendedor, esquema=EsquemaComision.objects.get(nombre="Visado"),
                                          vigente_desde=date(2026, 1, 1))
        AsignacionHorario.objects.create(empleado=cls.vendedor, horario=Horario.objects.get(nombre="Jornada estándar"),
                                         vigente_desde=date(2026, 1, 1))
        cls.admin = Usuario.objects.create_user("admin1", None, "clave-segura-123", rol=Rol.ADMINISTRADOR)

    def venta_cerrada(self, valor, n):
        cliente = Cliente.objects.create(identificacion=f"C-{n}", nombres="Cliente", apellidos=str(n), telefono="0999999999")
        dami = FormularioDAMI.objects.create(vendedor=self.vendedor, cliente=cliente, estado="ENVIADO", enviado_en=timezone.now())
        GestionCierre.objects.create(formulario=dami, encargado=self.encargado, resultado="VENTA_CERRADA", valor_venta=D(valor))

    def novedad(self, dia, tipo, estado="PENDIENTE", minutos=None):
        revisada = {} if estado == "PENDIENTE" else {"revisada_en": timezone.now(), "revisada_por": self.admin}
        if estado == "JUSTIFICADA":  # la base exige un respaldo para justificar
            revisada["soporte"] = Documento.objects.create(
                empleado=self.vendedor, tipo=TipoDocumento.objects.get(codigo="SOPORTE_ASISTENCIA"), archivo=f"soporte{dia}.pdf",
                nombre_original=f"soporte{dia}.pdf", tipo_mime="application/pdf", tamano_bytes=10, hash_sha256=f"{dia:064x}",
            )
        return NovedadAsistencia.objects.create(empleado=self.vendedor, fecha=self.inicio + timedelta(days=dia),
                                                tipo=tipo, estado=estado, minutos=minutos, **revisada)

    def estimar(self):
        return calculo.estimar_mes(self.vendedor, self.anio, self.mes)

    def test_comision_sobre_ventas_cerradas_va_aparte_del_rol(self):
        self.venta_cerrada("1500.00", 1)
        self.venta_cerrada("2000.00", 2)
        e = self.estimar()
        self.assertEqual(e.total_ventas, D("3500.00"))
        self.assertEqual((e.porcentaje_comision, e.comision), (D("15"), D("525.00")))
        self.assertEqual((e.total_ingresos, e.neto), (D("400.00"), D("400.00")))  # el rol no lleva la comisión
        self.assertEqual(e.total_a_pagar, D("925.00"))  # pero la empresa la paga

    def test_solo_vendedores_cobran_comision(self):
        HistorialSalario.objects.create(empleado=self.encargado, salario_base=D("500.00"), vigente_desde=date(2026, 1, 1))
        e = calculo.estimar_mes(self.encargado, self.anio, self.mes)
        self.assertFalse(e.es_vendedor)
        self.assertEqual((e.comision, e.neto), (D("0.00"), D("500.00")))

    def test_penalizaciones(self):
        self.novedad(0, "AUSENCIA", "NO_JUSTIFICADA")          # 10 % de 400 = 40
        self.novedad(1, "AUSENCIA", "JUSTIFICADA")             # 13,33
        self.novedad(2, "ATRASO", "NO_JUSTIFICADA", minutos=1)  # 1
        self.novedad(3, "ATRASO", "PENDIENTE", minutos=7)       # 5 (sin clasificar: se cobra y se avisa)
        self.novedad(4, "ATRASO", "JUSTIFICADA", minutos=8)     # justificado: nada
        self.novedad(5, "ATRASO", "NO_JUSTIFICADA", minutos=25)  # llamado de atención, sin multa
        e = self.estimar()
        self.assertEqual([d.valor for d in e.descuentos], [D("40.00"), D("13.33"), D("1.00"), D("5.00")])
        self.assertEqual(e.descuentos[0].concepto, "Falta no justificada (10 % del sueldo)")
        self.assertEqual(e.total_descuentos, D("59.33"))
        self.assertEqual(e.neto, D("340.67"))
        self.assertEqual(e.pendientes, 1)
        self.assertEqual([n.minutos for n in e.llamados_mes], [25])
        self.assertTrue(e.multas_superan_aviso)  # 59,33 > 10 % de 400

    def test_tres_llamados_de_atencion_son_visto_bueno(self):
        self.novedad(0, "ATRASO", "NO_JUSTIFICADA", minutos=15)
        self.novedad(1, "ATRASO", "PENDIENTE", minutos=40)
        self.assertFalse(self.estimar().requiere_visto_bueno)
        ObservacionEmpleado.objects.create(empleado=self.vendedor, tipo="LLAMADO_ATENCION", texto="Uso del celular", autor=self.admin)
        e = self.estimar()
        self.assertEqual(e.llamados_ventana, 3)
        self.assertTrue(e.requiere_visto_bueno)

    # ----------------------------------------------------------- pantallas (criterios de SCRUM-14)
    def test_ficha_muestra_sueldo_y_estimacion(self):
        self.client.force_login(self.admin)
        ficha = self.client.get(reverse("personal:empleado_detalle", args=[self.vendedor.pk]))
        self.assertContains(ficha, 'data-pestana="sueldo"')
        self.assertContains(ficha, "Visado")
        self.assertContains(ficha, reverse("nomina:rol", args=[self.vendedor.pk]))  # el detalle del pago está en Nómina
        self.assertNotContains(ficha, "Total descuentos")

    def test_cambiar_sueldo_conserva_historial_con_vigencia(self):
        self.client.force_login(self.admin)
        url = reverse("personal:salario_cambiar", args=[self.vendedor.pk])
        self.client.post(url, {"salario_base": "450.00", "vigente_desde": "2026-06-01", "motivo": "Aumento"})
        historial = list(self.vendedor.salarios.order_by("vigente_desde").values_list("salario_base", "vigente_desde", "vigente_hasta"))
        self.assertEqual(historial, [(D("400.00"), date(2026, 1, 1), date(2026, 5, 31)), (D("450.00"), date(2026, 6, 1), None)])
        # No se puede crear un cambio anterior al sueldo vigente.
        self.client.post(url, {"salario_base": "500.00", "vigente_desde": "2026-03-01"})
        self.assertEqual(self.vendedor.salarios.count(), 2)

    def test_cambiar_equipo(self):
        self.client.force_login(self.admin)
        baymont = EsquemaComision.objects.get(nombre="Paquetería")
        self.client.post(reverse("personal:equipo_cambiar", args=[self.vendedor.pk]), {"esquema": baymont.pk, "vigente_desde": "2026-03-01"})
        actual = self.vendedor.comisiones.get(vigente_hasta__isnull=True)
        self.assertEqual(actual.esquema, baymont)
        self.assertEqual(self.vendedor.comisiones.get(esquema__nombre="Visado").vigente_hasta, date(2026, 2, 28))

    def test_solo_autorizados_ven_y_editan_sueldos(self):
        vendedor_usuario = Usuario.objects.create_user("vend1", None, "clave-segura-123", rol=Rol.VENDEDOR)
        self.client.force_login(vendedor_usuario)
        respuesta = self.client.post(reverse("personal:salario_cambiar", args=[self.vendedor.pk]),
                                     {"salario_base": "9999", "vigente_desde": "2026-06-01"})
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self.vendedor.salarios.count(), 1)


    # ----------------------------------------------------------- módulo Nómina
    def test_nomina_resume_a_todos_con_totales(self):
        HistorialSalario.objects.create(empleado=self.encargado, salario_base=D("500.00"), vigente_desde=date(2026, 1, 1))
        self.venta_cerrada("2000.00", 1)                         # Visado 11 % = 220
        self.novedad(0, "AUSENCIA", "NO_JUSTIFICADA")            # − 40
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("nomina:resumen"), {"mes": f"{self.anio}-{self.mes:02d}"})
        nomina = respuesta.context["nomina"]
        self.assertEqual(len(nomina.roles), 2)
        self.assertEqual(nomina.total_sueldos, D("900.00"))
        self.assertEqual(nomina.total_comisiones, D("220.00"))
        self.assertEqual(nomina.total_descuentos, D("40.00"))
        self.assertEqual(nomina.total_neto, D("860.00"))
        self.assertEqual(nomina.total_a_pagar, D("1080.00"))
        self.assertContains(respuesta, "Vera Ana")
        self.assertContains(respuesta, reverse("nomina:comision_pdf", args=[self.vendedor.pk]))
        self.assertContains(respuesta, reverse("nomina:rol_pdf", args=[self.vendedor.pk]))

    def test_nomina_filtra_por_equipo_y_busqueda(self):
        self.client.force_login(self.admin)
        visas = EsquemaComision.objects.get(nombre="Visado")
        roles = self.client.get(reverse("nomina:resumen"), {"equipo": visas.pk}).context["roles"]
        self.assertEqual([r.empleado for r in roles], [self.vendedor])
        roles = self.client.get(reverse("nomina:resumen"), {"q": "paz"}).context["roles"]
        self.assertEqual([r.empleado for r in roles], [self.encargado])

    def test_nomina_no_incluye_a_quien_salio_antes_del_mes(self):
        Empleado.objects.filter(pk=self.encargado.pk).update(estado="DESVINCULADO", fecha_salida=date(2026, 1, 31))
        empleados = list(calculo.empleados_del_mes(self.anio, self.mes))
        self.assertNotIn(self.encargado, empleados)
        self.assertIn(self.encargado, list(calculo.empleados_del_mes(2026, 1)))

    def test_rol_de_pagos_y_pdf(self):
        import io
        from pypdf import PdfReader
        self.venta_cerrada("1500.00", 1)
        self.client.force_login(self.admin)
        mes = f"{self.anio}-{self.mes:02d}"
        rol = self.client.get(reverse("nomina:rol", args=[self.vendedor.pk]), {"mes": mes})
        self.assertContains(rol, "Neto a recibir")
        self.assertContains(rol, "$ 400,00")
        self.assertContains(rol, "se paga aparte")
        self.assertContains(rol, "$ 165,00")  # 11 % de 1.500, fuera del neto
        self.assertNotContains(rol, "$ 565,00")
        pdf = self.client.get(reverse("nomina:rol_pdf", args=[self.vendedor.pk]), {"mes": mes})
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        lector = PdfReader(io.BytesIO(pdf.content))
        self.assertEqual(len(lector.pages), 1)
        texto = lector.pages[0].extract_text()
        for esperado in ("ROL DE PAGOS", "Periodo:", "Ana Vera", "1710034065", "Visado", "NETO A RECIBIR", "$ 400,00", "Firma Empleado",
                         "Firma Contabilidad", "Faltas justificadas", "Días trabajados"):
            self.assertIn(esperado, texto)
        for ausente in ("Comisión", "165,00", "565,00"):
            self.assertNotIn(ausente, texto)

    def test_comprobante_de_comision_aparte(self):
        import io
        from pypdf import PdfReader
        self.venta_cerrada("1500.00", 1)
        self.client.force_login(self.admin)
        mes = f"{self.anio}-{self.mes:02d}"
        url = reverse("nomina:comision_pdf", args=[self.vendedor.pk])
        texto = PdfReader(io.BytesIO(self.client.get(url, {"mes": mes}).content)).pages[0].extract_text()
        for esperado in ("COMPROBANTE DE COMISIÓN", "Ana Vera", "Visado", "$ 1.500,00", "11 %", "COMISIÓN A RECIBIR", "$ 165,00", "Firma Contabilidad"):
            self.assertIn(esperado, texto)
        self.assertEqual(self.client.get(reverse("nomina:comision_pdf", args=[self.encargado.pk])).status_code, 404)

        reporte = reverse("nomina:reporte_roles")
        comisiones = self.client.get(reporte, {"mes": mes, "formato": "comisiones"})
        self.assertContains(comisiones, "Comprobante de comisión")
        self.assertContains(comisiones, "$165,00")
        self.assertEqual([r.empleado for r in comisiones.context["nomina"].roles], [self.vendedor])  # solo vendedores
        roles = self.client.get(reporte, {"mes": mes, "formato": "individual"})
        self.assertNotContains(roles, "165,00")
        self.assertNotContains(self.client.get(reporte, {"mes": mes}), "165,00")  # tampoco en el cuadro general

    def test_nomina_solo_para_autorizados(self):
        self.client.force_login(Usuario.objects.create_user("vend2", None, "clave-segura-123", rol=Rol.VENDEDOR))
        self.assertEqual(self.client.get(reverse("nomina:resumen")).status_code, 403)
        self.assertEqual(self.client.get(reverse("nomina:rol_pdf", args=[self.vendedor.pk])).status_code, 403)
