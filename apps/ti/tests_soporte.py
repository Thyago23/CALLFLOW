"""Mesa de ayuda, licencias, mantenimiento preventivo, accesos y bloqueos, historial de respaldos y avisos a TI."""
import json
from io import StringIO
import shutil
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path

from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.utils import InternalError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core import limite_peticiones
from apps.core.models import Notificacion

from . import alertas, ingresos, mantenimiento, respaldos, soporte
from .models import AvisoTI, ComentarioTicket, Equipo, EventoEquipo, Licencia, PlanMantenimiento, Ticket
from .servicios import OperacionNoPermitida
from .tests import CLAVE, BaseTI


def avisos_ti(usuario, tipo=Notificacion.Tipo.AVISO_TI):
    return Notificacion.objects.filter(destinatario=usuario, tipo=tipo)


# --------------------------------------------------------------------------- mesa de ayuda
class MesaAyudaTests(BaseTI):
    def setUp(self):
        self.client.force_login(self.vendedor)

    def reportar(self, **extra):
        datos = {"categoria": "AUDIO", "titulo": "No tengo audio", "descripcion": "La diadema no suena", "prioridad": "ALTA"} | extra
        return self.client.post(reverse("soporte:crear"), datos)

    def test_cualquier_empleado_ve_la_mesa_de_ayuda_en_el_menu(self):
        for usuario in (self.vendedor, self.admin, self.santiago):
            self.client.force_login(usuario)
            self.assertContains(self.client.get(reverse("inicio")), reverse("soporte:inicio"))

    def test_reportar_avisa_a_ti_reconoce_el_equipo_y_fija_los_plazos(self):
        equipo = Equipo.objects.create(codigo="EQ-0100", tipo="ESCRITORIO", direccion_ip="127.0.0.1")
        antes = timezone.now()
        respuesta = self.reportar()
        ticket = Ticket.objects.get()
        self.assertRedirects(respuesta, reverse("soporte:detalle", args=[ticket.pk]))
        self.assertEqual((ticket.reportado_por, ticket.equipo, ticket.estado), (self.vendedor, equipo, Ticket.Estado.ABIERTO))
        respuesta_max, solucion_max = soporte.plazos(Ticket.Prioridad.ALTA)
        self.assertAlmostEqual((ticket.limite_respuesta - antes).total_seconds(), respuesta_max.total_seconds(), delta=5)
        self.assertAlmostEqual((ticket.limite_solucion - antes).total_seconds(), solucion_max.total_seconds(), delta=5)
        for ti in (self.santiago, self.nicolas):
            self.assertTrue(avisos_ti(ti, Notificacion.Tipo.SOPORTE).filter(titulo__contains=ticket.numero).exists())
        self.assertEqual(soporte.sin_atender(), 1)

    def test_si_no_prende_se_toma_el_equipo_asignado_y_no_el_de_la_ip(self):
        Equipo.objects.create(codigo="EQ-0101", tipo="ESCRITORIO", direccion_ip="127.0.0.1")  # el del compañero
        suyo = Equipo.objects.create(codigo="EQ-0102", tipo="ESCRITORIO", asignado_a=self.empleado, ubicacion="Puesto 12")
        self.reportar(categoria="NO_ENCIENDE", titulo="No prende mi PC")
        ticket = Ticket.objects.get()
        self.assertEqual((ticket.equipo, ticket.desde_otro_equipo, ticket.puesto), (suyo, True, "Puesto 12"))

    def test_desde_el_celular_tampoco_se_usa_la_ip(self):
        Equipo.objects.create(codigo="EQ-0101", tipo="ESCRITORIO", direccion_ip="127.0.0.1")
        suyo = Equipo.objects.create(codigo="EQ-0102", tipo="LAPTOP", asignado_a=self.empleado)
        self.client.post(reverse("soporte:crear"), {"categoria": "INTERNET", "titulo": "Sin internet", "prioridad": "ALTA"},
                         HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) Mobile Safari/537.36")
        ticket = Ticket.objects.get()
        self.assertEqual((ticket.equipo, ticket.desde_otro_equipo), (suyo, True))

    def test_ti_registra_el_reporte_de_quien_aviso_en_persona(self):
        suyo = Equipo.objects.create(codigo="EQ-0102", tipo="ESCRITORIO", asignado_a=self.empleado)
        Equipo.objects.create(codigo="EQ-0103", tipo="ESCRITORIO", direccion_ip="127.0.0.1")  # el de TI
        self.client.force_login(self.santiago)
        self.assertContains(self.client.get(reverse("soporte:crear")), "¿Quién avisó?")
        self.client.post(reverse("soporte:crear"), {"reportado_por": self.vendedor.pk, "categoria": "NO_ENCIENDE",
                                                    "titulo": "No prende", "prioridad": "CRITICA"})
        ticket = Ticket.objects.get()
        self.assertEqual((ticket.reportado_por, ticket.registrado_por, ticket.equipo, ticket.ip, ticket.prioridad),
                         (self.vendedor, self.santiago, suyo, None, "CRITICA"))
        self.assertTrue(avisos_ti(self.vendedor, Notificacion.Tipo.SOPORTE).filter(titulo__contains="TI registró").exists())
        self.assertTrue(avisos_ti(self.nicolas, Notificacion.Tipo.SOPORTE).exists())
        self.client.force_login(self.vendedor)  # la persona lo ve como suyo
        self.assertEqual(self.client.get(reverse("soporte:detalle", args=[ticket.pk])).status_code, 200)

    def test_el_formulario_explica_que_hacer_si_el_equipo_no_funciona(self):
        pagina = self.client.get(reverse("soporte:crear"))
        self.assertContains(pagina, "avise en persona a TI")
        self.assertNotContains(pagina, "¿Quién avisó?")
        self.assertNotContains(pagina, "---------")

    def test_la_prioridad_critica_solo_la_pone_ti(self):
        self.reportar(prioridad="CRITICA")
        self.assertFalse(Ticket.objects.exists())  # no es una opción válida en el formulario
        ticket = soporte.crear_ticket(usuario=self.vendedor, titulo="Caída", categoria="INTERNET", prioridad="CRITICA")
        self.assertEqual(ticket.prioridad, Ticket.Prioridad.ALTA)

    def test_nadie_ve_el_reporte_de_otra_persona(self):
        ticket = soporte.crear_ticket(usuario=self.admin, titulo="Impresora", categoria="IMPRESORA", prioridad="BAJA")
        self.assertEqual(self.client.get(reverse("soporte:detalle", args=[ticket.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("soporte:inicio")), "Impresora")

    def test_respuesta_de_ti_cuenta_como_primera_respuesta_y_avisa(self):
        ticket = soporte.crear_ticket(usuario=self.vendedor, titulo="Sin internet", categoria="INTERNET", prioridad="MEDIA")
        soporte.comentar(ticket, self.santiago, "Nota solo para TI", interno=True)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.primera_respuesta_en)  # las notas internas no cuentan
        soporte.comentar(ticket, self.santiago, "Voy a su puesto")
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.primera_respuesta_en)
        self.assertEqual((ticket.estado, ticket.asignado_a), (Ticket.Estado.EN_PROCESO, self.santiago))
        self.assertFalse(ticket.respuesta_atrasada)
        self.assertTrue(avisos_ti(self.vendedor, Notificacion.Tipo.SOPORTE).filter(titulo__contains="respondió").exists())
        pagina = self.client.get(reverse("soporte:detalle", args=[ticket.pk]))
        self.assertContains(pagina, "Voy a su puesto")
        self.assertNotContains(pagina, "Nota solo para TI")

    def test_resolver_exige_solucion_y_quien_reporto_confirma(self):
        ticket = soporte.crear_ticket(usuario=self.vendedor, titulo="Sin audio", categoria="AUDIO", prioridad="ALTA")
        with self.assertRaises(OperacionNoPermitida):
            soporte.gestionar(ticket, self.santiago, estado="RESUELTO", prioridad="ALTA")
        soporte.gestionar(ticket, self.santiago, estado="RESUELTO", prioridad="ALTA", solucion="Se cambió la diadema")
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.resuelto_en)
        self.assertIsNotNone(ticket.primera_respuesta_en)
        self.assertContains(self.client.get(reverse("soporte:detalle", args=[ticket.pk])), "¿Quedó bien?")

        self.client.post(reverse("soporte:confirmar", args=[ticket.pk]), {"solucionado": "no", "texto": "Sigue sin sonar"})
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.ABIERTO)
        self.assertIsNone(ticket.resuelto_en)
        self.assertTrue(avisos_ti(self.santiago, Notificacion.Tipo.SOPORTE).filter(titulo__contains="sigue fallando").exists())

        soporte.gestionar(ticket, self.santiago, estado="RESUELTO", prioridad="ALTA", solucion="Se cambió el puerto USB")
        self.client.post(reverse("soporte:confirmar", args=[ticket.pk]), {"solucionado": "si"})
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, Ticket.Estado.CERRADO)
        self.assertIsNotNone(ticket.cerrado_en)
        # Cerrado: quien reportó ya no puede escribir.
        with self.assertRaises(OperacionNoPermitida):
            soporte.comentar(ticket, self.vendedor, "Hola")

    def test_cambiar_la_prioridad_recalcula_los_plazos(self):
        ticket = soporte.crear_ticket(usuario=self.vendedor, titulo="Lento", categoria="COMPUTADOR", prioridad="BAJA")
        soporte.gestionar(ticket, self.santiago, estado="EN_PROCESO", prioridad="CRITICA")
        ticket.refresh_from_db()
        self.assertEqual(ticket.limite_respuesta, ticket.creado_en + soporte.plazos("CRITICA")[0])
        self.assertIn("Prioridad: Baja → Crítica", ticket.comentarios.last().cambio)

    def test_solo_ti_gestiona(self):
        ticket = soporte.crear_ticket(usuario=self.vendedor, titulo="Lento", categoria="COMPUTADOR", prioridad="BAJA")
        respuesta = self.client.post(reverse("soporte:gestionar", args=[ticket.pk]), {"estado": "CERRADO", "prioridad": "BAJA",
                                                                                     "solucion": "x"})
        self.assertEqual(respuesta.status_code, 403)

    def test_la_bandeja_de_ti_muestra_indicadores(self):
        a = soporte.crear_ticket(usuario=self.vendedor, titulo="Sin audio", categoria="AUDIO", prioridad="ALTA")
        soporte.crear_ticket(usuario=self.admin, titulo="Sin internet", categoria="INTERNET", prioridad="MEDIA")
        soporte.gestionar(a, self.santiago, estado="RESUELTO", prioridad="ALTA", solucion="Listo")
        datos = soporte.indicadores(30)
        self.assertEqual((datos["total"], datos["resueltos"], datos["pct_respuesta"], datos["pct_solucion"]), (2, 1, 100, 100))
        self.client.force_login(self.santiago)
        pagina = self.client.get(reverse("soporte:inicio"))
        self.assertContains(pagina, "Sin internet")
        self.assertContains(pagina, "Respuesta a tiempo")

    def test_la_base_exige_solucion_y_el_seguimiento_no_se_edita(self):
        ticket = soporte.crear_ticket(usuario=self.vendedor, titulo="Sin audio", categoria="AUDIO", prioridad="ALTA")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ticket.objects.filter(pk=ticket.pk).update(estado="RESUELTO", resuelto_en=timezone.now())
        comentario = soporte.comentar(ticket, self.vendedor, "¿Alguna novedad?")
        with self.assertRaises((IntegrityError, InternalError)), transaction.atomic():
            ComentarioTicket.objects.filter(pk=comentario.pk).update(texto="otra cosa")
        with self.assertRaises((IntegrityError, InternalError)), transaction.atomic():
            ComentarioTicket.objects.filter(pk=comentario.pk).delete()


# --------------------------------------------------------------------------- licencias y servicios
class LicenciasTests(BaseTI):
    def test_registrar_desde_la_pagina(self):
        respuesta = self.client.post(reverse("ti:licencia_crear"), {
            "tipo": "ANTIVIRUS", "nombre": "ESET Endpoint", "proveedor": "ESET", "cantidad": "20",
            "fecha_vencimiento": "2027-03-01", "dias_aviso": "30", "activa": "on"})
        self.assertRedirects(respuesta, reverse("ti:licencias"))
        self.assertContains(self.client.get(reverse("ti:licencias")), "ESET Endpoint")

    def test_no_se_guarda_una_clave_de_producto_completa(self):
        respuesta = self.client.post(reverse("ti:licencia_crear"), {
            "tipo": "WINDOWS", "nombre": "Windows 11 Pro", "referencia": "ABCDE-12345-FGHIJ-67890-KLMNO", "dias_aviso": "30"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(Licencia.objects.exists())

    def test_avisa_una_vez_antes_y_otra_al_vencer_y_se_rearma_al_renovar(self):
        hoy = timezone.localdate()
        lic = Licencia.objects.create(tipo="DOMINIO", nombre="multivisa.com.ec", fecha_vencimiento=hoy + timedelta(days=10),
                                      dias_aviso=15)
        Licencia.objects.create(tipo="OFFICE", nombre="Lejana", fecha_vencimiento=hoy + timedelta(days=800))
        Licencia.objects.create(tipo="OFFICE", nombre="Ya no se usa", fecha_vencimiento=hoy, activa=False)
        self.assertEqual(alertas.revisar_licencias(hoy), 1)
        self.assertEqual(alertas.revisar_licencias(hoy), 0)  # no se repite
        self.assertTrue(avisos_ti(self.santiago).filter(titulo="multivisa.com.ec vence en 10 días").exists())
        self.assertEqual(alertas.revisar_licencias(hoy + timedelta(days=11)), 1)  # ya venció
        self.assertTrue(avisos_ti(self.nicolas).filter(titulo="Venció multivisa.com.ec").exists())
        lic.fecha_vencimiento = hoy + timedelta(days=375)
        lic.save()
        self.assertEqual(alertas.revisar_licencias(hoy + timedelta(days=365)), 1)  # vencimiento nuevo, aviso nuevo
        self.assertEqual(lic.situacion, "vigente")

    def test_la_base_valida_fechas_y_dias_de_aviso(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Licencia.objects.create(tipo="OTRO", nombre="x", fecha_inicio=date(2026, 5, 1), fecha_vencimiento=date(2026, 4, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Licencia.objects.create(tipo="OTRO", nombre="x", dias_aviso=400)

    def test_solo_ti(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("ti:licencias")).status_code, 403)


# --------------------------------------------------------------------------- mantenimiento preventivo
class MantenimientoTests(BaseTI):
    def setUp(self):
        super().setUp()
        self.pc1 = Equipo.objects.create(codigo="EQ-0201", tipo="ESCRITORIO")
        self.pc2 = Equipo.objects.create(codigo="EQ-0202", tipo="ESCRITORIO")
        self.baja = Equipo.objects.create(codigo="EQ-0203", tipo="ESCRITORIO", estado="BAJA")
        self.diadema = Equipo.objects.create(codigo="EQ-0204", tipo="DIADEMA")

    def test_sumar_meses_respeta_el_fin_de_mes(self):
        self.assertEqual(mantenimiento.sumar_meses(date(2026, 8, 31), 6), date(2027, 2, 28))
        self.assertEqual(mantenimiento.sumar_meses(date(2026, 11, 16), 6), date(2027, 5, 16))
        self.assertEqual(mantenimiento.sumar_meses(date(2026, 12, 1), 1), date(2027, 1, 1))

    def test_programar_para_todos_los_de_un_tipo(self):
        PlanMantenimiento.objects.create(equipo=self.pc2, actividad="Limpieza", cada_meses=6, proxima_fecha=date(2027, 1, 1))
        respuesta = self.client.post(reverse("ti:plan_crear"), {
            "tipo": "ESCRITORIO", "actividad": "limpieza", "cada_meses": "6", "primera_fecha": "2026-12-01", "dias_aviso": "7"})
        self.assertRedirects(respuesta, reverse("ti:mantenimiento"))
        self.assertEqual(sorted(PlanMantenimiento.objects.values_list("equipo__codigo", flat=True)), ["EQ-0201", "EQ-0202"])

    def test_marcar_hecho_queda_en_la_hoja_de_vida_y_programa_el_siguiente(self):
        plan = PlanMantenimiento.objects.create(equipo=self.pc1, actividad="Limpieza", cada_meses=6, proxima_fecha=date(2026, 11, 1))
        hoy = timezone.localdate()
        respuesta = self.client.post(reverse("ti:plan_realizado", args=[plan.pk]),
                                     {"fecha": hoy.isoformat(), "realizado_por": "Santiago", "descripcion": "Se cambió la pasta"})
        self.assertRedirects(respuesta, reverse("ti:mantenimiento"))
        plan.refresh_from_db()
        self.assertEqual((plan.ultima_fecha, plan.proxima_fecha), (hoy, mantenimiento.sumar_meses(hoy, 6)))
        evento = EventoEquipo.objects.get(equipo=self.pc1, tipo=EventoEquipo.Tipo.MANTENIMIENTO)
        self.assertIn("Se cambió la pasta", evento.descripcion)
        with self.assertRaises(OperacionNoPermitida):
            mantenimiento.registrar_realizado(plan, fecha=hoy - timedelta(days=1))  # antes del último registrado

    def test_avisa_antes_y_cuando_se_atrasa_pero_no_por_equipos_de_baja(self):
        hoy = timezone.localdate()
        PlanMantenimiento.objects.create(equipo=self.pc1, actividad="Limpieza", cada_meses=6, proxima_fecha=hoy + timedelta(days=3))
        PlanMantenimiento.objects.create(equipo=self.pc2, actividad="Limpieza", cada_meses=6, proxima_fecha=hoy - timedelta(days=2))
        PlanMantenimiento.objects.create(equipo=self.baja, actividad="Limpieza", cada_meses=6, proxima_fecha=hoy - timedelta(days=2))
        PlanMantenimiento.objects.create(equipo=self.diadema, actividad="Revisión", cada_meses=6, proxima_fecha=hoy + timedelta(days=60))
        self.assertEqual(alertas.revisar_mantenimientos(hoy), 2)
        self.assertEqual(alertas.revisar_mantenimientos(hoy), 0)
        titulos = set(avisos_ti(self.santiago).values_list("titulo", flat=True))
        self.assertEqual(titulos, {"Mantenimiento en 3 días: EQ-0201", "Mantenimiento atrasado: EQ-0202"})
        pagina = self.client.get(reverse("ti:mantenimiento"))
        self.assertContains(pagina, "EQ-0202")
        self.assertNotContains(pagina, "EQ-0203")
        self.assertContains(self.client.get(reverse("ti:detalle", args=[self.pc1.pk])), "Mantenimiento programado")

    def test_la_base_valida_la_frecuencia_y_no_repite_actividad(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlanMantenimiento.objects.create(equipo=self.pc1, actividad="x", cada_meses=0, proxima_fecha=date(2027, 1, 1))
        PlanMantenimiento.objects.create(equipo=self.pc1, actividad="x", cada_meses=6, proxima_fecha=date(2027, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlanMantenimiento.objects.create(equipo=self.pc1, actividad="x", cada_meses=3, proxima_fecha=date(2027, 1, 1))


# --------------------------------------------------------------------------- accesos y bloqueos
@override_settings(LIMITE_PETICIONES_ACTIVO=True, LIMITE_FALLOS_INGRESO=(3, 900, 900))
class AccesosTests(BaseTI):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_tras_varios_fallos_la_cuenta_aparece_bloqueada_y_ti_la_desbloquea(self):
        nombre = f"prueba.{uuid.uuid4().hex[:8]}"
        self.vendedor.username = nombre
        self.vendedor.save()
        for _ in range(3):
            limite_peticiones.registrar_ingreso_fallido(nombre)
        self.assertGreater(limite_peticiones.ingreso_bloqueado(nombre), 0)
        self.assertIn(nombre, [b["usuario"] for b in ingresos.cuentas_bloqueadas()])
        self.assertTrue(avisos_ti(self.santiago).filter(titulo=f"Cuenta bloqueada: {nombre}").exists())
        intentos = ingresos.intentos_fallidos(1)
        fila = next(u for u in intentos["por_usuario"] if u["usuario"] == nombre)
        self.assertEqual((fila["fallos"], fila["bloqueos"], fila["cuenta"]), (2, 1, self.vendedor))
        self.assertContains(self.client.get(reverse("ti:accesos")), nombre)

        self.client.post(reverse("ti:desbloquear"), {"usuario": nombre})
        self.assertEqual(limite_peticiones.ingreso_bloqueado(nombre), 0)
        self.assertNotIn(nombre, [b["usuario"] for b in ingresos.cuentas_bloqueadas()])
        self.client.logout()
        respuesta = self.client.post(reverse("login"), {"username": nombre, "password": CLAVE})
        self.assertEqual(respuesta.status_code, 302)

    def test_usuarios_que_no_existen_se_marcan(self):
        nombre = f"inventado.{uuid.uuid4().hex[:8]}"
        limite_peticiones.registrar_ingreso_fallido(nombre)
        fila = next(u for u in ingresos.intentos_fallidos(1)["por_usuario"] if u["usuario"] == nombre)
        self.assertIsNone(fila["cuenta"])

    def test_solo_ti(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("ti:accesos")).status_code, 403)
        self.assertEqual(self.client.post(reverse("ti:desbloquear"), {"usuario": "x"}).status_code, 403)


# --------------------------------------------------------------------------- respaldos y disco
class RespaldosTests(BaseTI):
    def setUp(self):
        super().setUp()
        self.logs = Path(tempfile.mkdtemp(prefix="callflow-logs-respaldos-"))
        self.carpeta = Path(tempfile.mkdtemp(prefix="callflow-respaldos-"))
        for carpeta in (self.logs, self.carpeta):
            self.addCleanup(shutil.rmtree, carpeta, ignore_errors=True)
        ajustes = override_settings(LOGS_DIR=self.logs, CARPETA_RESPALDOS=self.carpeta)
        ajustes.enable()
        self.addCleanup(ajustes.disable)

    def corrida(self, horas_atras, resultado="ok", tipo="diario", mensaje=""):
        fecha = (timezone.localtime() - timedelta(hours=horas_atras)).isoformat(timespec="seconds")
        linea = {"fecha": fecha, "tipo": tipo, "resultado": resultado, "archivo": "callflow_x.dump", "tamano": 1024,
                 "duracion_s": 12, "copia": "", "mensaje": mensaje}
        with (self.logs / respaldos.NOMBRE_ARCHIVO).open("a", encoding="utf-8") as f:
            f.write(json.dumps(linea) + "\n")

    def test_al_dia(self):
        self.corrida(2)
        self.assertEqual(respaldos.estado()["nivel"], "ok")
        self.assertEqual(alertas.revisar_respaldos(), 0)
        self.assertContains(self.client.get(reverse("ti:respaldos")), "al día")

    def test_un_fallo_se_muestra_y_avisa_una_sola_vez(self):
        self.corrida(5)
        self.corrida(1, resultado="fallo", tipo="hora", mensaje="pg_dump terminó con error (1)")
        estado = respaldos.estado()
        self.assertEqual((estado["nivel"], estado["fallos"]), ("fallo", 1))
        self.assertEqual(alertas.revisar_respaldos(), 1)
        self.assertEqual(alertas.revisar_respaldos(), 0)
        self.assertTrue(avisos_ti(self.santiago).filter(titulo="Falló el respaldo automático").exists())
        self.assertContains(self.client.get(reverse("ti:respaldos")), "pg_dump terminó con error (1)")
        self.corrida(0, tipo="hora")  # la siguiente salió bien
        self.assertEqual(respaldos.estado()["nivel"], "ok")

    def test_atrasado_o_sin_respaldos(self):
        self.assertEqual(respaldos.estado()["nivel"], "sin_datos")
        self.corrida(24 * 3)
        self.assertEqual(respaldos.estado()["nivel"], "atrasado")
        self.assertEqual(alertas.revisar_respaldos(), 1)
        self.assertTrue(avisos_ti(self.nicolas).filter(titulo="Los respaldos están atrasados").exists())

    def test_lineas_dañadas_se_ignoran(self):
        (self.logs / respaldos.NOMBRE_ARCHIVO).write_text("{no es json\n", encoding="utf-8")
        self.corrida(1)
        self.assertEqual(len(respaldos.historial()), 1)


class AvisosTests(BaseTI):
    def test_el_comando_corre_todas_las_revisiones(self):
        Licencia.objects.create(tipo="INTERNET", nombre="Fibra 300", fecha_vencimiento=timezone.localdate())
        call_command("revisar_alertas_ti", stdout=StringIO())
        self.assertTrue(avisos_ti(self.santiago).filter(titulo="Fibra 300 vence hoy").exists())
        self.assertTrue(AvisoTI.objects.filter(clave__startswith="licencia:").exists())

    def test_un_aviso_solo_se_manda_una_vez(self):
        self.assertTrue(alertas.avisar("prueba:1", "Hola"))
        self.assertFalse(alertas.avisar("prueba:1", "Hola"))
        self.assertEqual(avisos_ti(self.santiago).filter(titulo="Hola").count(), 1)
        self.assertFalse(avisos_ti(self.vendedor).exists())  # solo a TI


class PanelTITests(BaseTI):
    def test_el_panel_resume_lo_pendiente(self):
        soporte.crear_ticket(usuario=self.vendedor, titulo="Sin audio", categoria="AUDIO", prioridad="ALTA")
        Licencia.objects.create(tipo="DOMINIO", nombre="multivisa.com.ec", fecha_vencimiento=timezone.localdate() + timedelta(days=5))
        pagina = self.client.get(reverse("ti:panel"))
        self.assertContains(pagina, "Sin audio")
        self.assertContains(pagina, "multivisa.com.ec")

    def test_menu_de_ti_ordenado_en_secciones(self):
        menu = self.client.get(reverse("ti:panel")).context["menu_secciones"]
        tecnologia = next(g for g in menu if g["nombre"] == "Tecnología")
        self.assertEqual([m.nombre for m in tecnologia["modulos"]], ["Panel de TI", "Mesa de ayuda", "Equipos y licencias", "Monitoreo"])
        pagina = self.client.get(reverse("ti:respaldos")).content.decode()
        self.assertIn('aria-current="page"', pagina)
        self.assertNotIn(reverse("ti:licencias") + '"><i', pagina.split("subnav-modulo")[1])  # solo las pestañas de Monitoreo

    def test_solo_ti(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("ti:panel")).status_code, 403)
