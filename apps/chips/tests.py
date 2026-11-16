"""Registro de chips: acceso exclusivo, reposiciones con cambio de número y reporte firmado."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.utils import InternalError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.concurrencia import huella_actual
from apps.core.models import Rol, Usuario

from . import servicios
from .forms import ChipForm
from .models import Chip, Reposicion


class BaseChips(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.coordinador = Usuario.objects.create_user("marlon.rosero", password="Clave-Segura-2026", first_name="Marlon",
                                                      last_name="Rosero de Mendoza", rol=Rol.ENCARGADO_CIERRE)
        cls.coordinador.user_permissions.add(Permission.objects.get(content_type__app_label="chips", codename="gestionar_chips"))
        cls.otro_cierre = Usuario.objects.create_user("ana.vera", password="Clave-Segura-2026", rol=Rol.ENCARGADO_CIERRE)
        cls.admin = Usuario.objects.create_superuser("admin.callflow", password="Clave-Segura-2026")

    def setUp(self):
        self.client.force_login(self.coordinador)

    def chip(self, numero="0991234567", **datos):
        return Chip.objects.create(numero=numero, registrado_por=self.coordinador, **datos)


class AccesoTests(BaseChips):
    def test_solo_el_coordinador_ve_el_modulo(self):
        self.assertContains(self.client.get(reverse("inicio")), reverse("chips:lista"))
        self.assertEqual(self.client.get(reverse("chips:lista")).status_code, 200)
        for usuario in (self.otro_cierre, self.admin):  # ni otro encargado de cierre ni el superusuario
            self.client.force_login(usuario)
            self.assertNotContains(self.client.get(reverse("inicio")), reverse("chips:lista"))
            for url in (reverse("chips:lista"), reverse("chips:crear"), reverse("chips:reporte")):
                self.assertEqual(self.client.get(url).status_code, 403, (usuario, url))

    def test_sin_sesion_va_al_ingreso(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("chips:lista")).status_code, 302)

    def test_comando_da_y_quita_el_acceso(self):
        from io import StringIO

        call_command("acceso_chips", "ana.vera", stdout=StringIO())
        self.client.force_login(self.otro_cierre)
        self.assertEqual(self.client.get(reverse("chips:lista")).status_code, 200)
        call_command("acceso_chips", "ana.vera", "--quitar", stdout=StringIO())
        self.otro_cierre = Usuario.objects.get(pk=self.otro_cierre.pk)
        self.client.force_login(self.otro_cierre)
        self.assertEqual(self.client.get(reverse("chips:lista")).status_code, 403)


class ChipTests(BaseChips):
    def test_registrar_chip(self):
        respuesta = self.client.post(reverse("chips:crear"), {"numero": "099 123 4567", "operadora": "CLARO", "iccid": "8959 3010 0000",
                                                             "observacion": ""})
        chip = Chip.objects.get()
        self.assertRedirects(respuesta, reverse("chips:detalle", args=[chip.pk]))
        self.assertEqual((chip.numero, chip.iccid, chip.registrado_por), ("0991234567", "895930100000", self.coordinador))

    def test_numero_invalido_o_repetido(self):
        self.assertContains(self.client.post(reverse("chips:crear"), {"numero": "12345", "operadora": "CLARO"}), "10 dígitos")
        self.chip()
        self.assertContains(self.client.post(reverse("chips:crear"), {"numero": "0991234567", "operadora": "CLARO"}), "Ya existe")

    def test_al_editar_no_se_cambia_el_numero(self):
        chip = self.chip()
        self.client.post(reverse("chips:editar", args=[chip.pk]), {"numero": "0999999999", "operadora": "SUPERMAXI", "estado": "ACTIVO"}
                         | huella_actual(ChipForm, chip))
        chip.refresh_from_db()
        self.assertEqual((chip.numero, chip.operadora), ("0991234567", "SUPERMAXI"))


class ReposicionTests(BaseChips):
    def reponer(self, chip, **datos):
        datos = {"fecha": timezone.localdate().isoformat(), "motivo": "PERDIDA", "operadora": "CLARO", **datos}
        return self.client.post(reverse("chips:reposicion", args=[chip.pk]), datos)

    def test_reposicion_con_el_mismo_numero(self):
        chip = self.chip(iccid="111")
        self.reponer(chip, iccid_nuevo="222", costo="5.50")
        r = Reposicion.objects.get()
        chip.refresh_from_db()
        self.assertEqual((r.numero_anterior, r.numero_nuevo, r.cambio_numero), ("0991234567", "0991234567", False))
        self.assertEqual((r.iccid_anterior, r.iccid_nuevo, chip.iccid, r.costo), ("111", "222", "222", Decimal("5.50")))

    def test_cambio_de_numero_queda_registrado(self):
        chip = self.chip()
        respuesta = self.reponer(chip, motivo="ROBO", cambia_numero="on", numero_nuevo="0987654321")
        self.assertRedirects(respuesta, reverse("chips:detalle", args=[chip.pk]))
        chip.refresh_from_db()
        r = Reposicion.objects.get()
        self.assertEqual(chip.numero, "0987654321")
        self.assertEqual((r.numero_anterior, r.numero_nuevo, r.cambio_numero), ("0991234567", "0987654321", True))
        detalle = self.client.get(reverse("chips:detalle", args=[chip.pk]))
        self.assertContains(detalle, "Número cambiado")
        # Se encuentra buscando por el número viejo.
        self.assertContains(self.client.get(reverse("chips:lista"), {"q": "0991234567"}), "0987654321")

    def test_cambio_de_numero_exige_numero_distinto_y_libre(self):
        chip = self.chip()
        self.chip(numero="0980000000")
        self.assertContains(self.reponer(chip, motivo="CAMBIO_NUMERO"), "Escriba el número nuevo")
        self.assertContains(self.reponer(chip, cambia_numero="on", numero_nuevo="0991234567"), "mismo número")
        self.assertContains(self.reponer(chip, cambia_numero="on", numero_nuevo="0980000000"), "ya está registrado en otro chip")
        self.assertFalse(Reposicion.objects.exists())

    def test_fecha_futura_y_chip_de_baja(self):
        chip = self.chip()
        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertContains(self.reponer(chip, fecha=manana), "no puede ser futura")
        chip.estado = Chip.Estado.BAJA
        chip.save()
        self.assertRedirects(self.reponer(chip), reverse("chips:detalle", args=[chip.pk]))
        self.assertFalse(Reposicion.objects.exists())

    def test_el_libro_no_se_edita_ni_se_borra(self):
        r = servicios.registrar_reposicion(self.chip(), fecha=timezone.localdate(), motivo="DANO", usuario=self.coordinador)
        for accion in (lambda: Reposicion.objects.filter(pk=r.pk).update(observacion="x"), lambda: Reposicion.objects.filter(pk=r.pk).delete()):
            with self.assertRaises((IntegrityError, InternalError)), transaction.atomic():
                accion()


class ReporteTests(BaseChips):
    def test_reporte_con_firmas_y_cambios(self):
        hoy = timezone.localdate()
        a, b = self.chip(), self.chip(numero="0970000000", operadora="SUPERMAXI")
        servicios.registrar_reposicion(a, fecha=hoy, motivo="ROBO", numero_nuevo="0960000000", costo=Decimal("3"), usuario=self.coordinador)
        servicios.registrar_reposicion(b, fecha=hoy, motivo="DANO", costo=Decimal("2.5"), usuario=self.coordinador)
        servicios.registrar_reposicion(b, fecha=date(2020, 1, 1), motivo="OTRO", usuario=self.coordinador)  # fuera del rango
        respuesta = self.client.get(reverse("chips:reporte"), {"desde": hoy.replace(day=1).isoformat(), "hasta": hoy.isoformat()})
        reporte = respuesta.context["reporte"]
        self.assertEqual((reporte.total, reporte.con_cambio_numero, reporte.mismo_numero, reporte.costo_total), (2, 1, 1, Decimal("5.5")))
        for texto in ("Reporte de reposiciones de chips", "0991234567", "0960000000", "Raul Tapia", "Dueño", "Diego Mendoza",
                      "Gerente de Talento Humano y Marketing", "Marlon Rosero de Mendoza", "Coordinador"):
            self.assertContains(respuesta, texto)
        solo_supermaxi = self.client.get(reverse("chips:reporte"), {"desde": hoy.isoformat(), "hasta": hoy.isoformat(), "operadora": "SUPERMAXI"})
        self.assertEqual(solo_supermaxi.context["reporte"].total, 1)
