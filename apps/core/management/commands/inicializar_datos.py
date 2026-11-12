"""
Carga los catálogos iniciales y los grupos de permisos por rol.
Es idempotente: se puede ejecutar varias veces sin duplicar nada.

    python manage.py inicializar_datos
"""
from datetime import time

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.asistencia.models import Horario
from apps.core import jornada
from apps.core.models import CUENTAS_TI, Rol, Usuario
from apps.documentos.models import TipoDocumento
from apps.inventario.models import CategoriaMaterial
from apps.nomina.models import TipoNovedadNomina
from apps.personal.models import Cargo
from apps.reportes.catalogo import TIPOS_REPORTE  # tipos de reporte y sus roles (4.5)
from apps.reportes.models import TipoReporte

CARGOS = ["Administrador", "Asesor de ventas", "Supervisor", "Imagen corporativa", "Tecnología (TI)"]

TIPOS_DOCUMENTO = [
    ("CEDULA", "Copia de cédula", True),
    ("HOJA_VIDA", "Hoja de vida", True),
    ("TITULO_BACHILLER", "Título de bachiller", True),
    ("CERT_ESTUDIOS", "Certificado de estudios", False),
    ("CERT_LABORAL", "Certificado laboral", False),
    ("CONTRATO", "Contrato de trabajo", False),
    ("SOPORTE_ASISTENCIA", "Soporte de justificación (asistencia)", False),
    ("FACTURA", "Factura para solicitud de pago", False),
    ("OTRO", "Otro documento", False),
]

TIPOS_NOVEDAD = [
    ("BONO", "Bono", "INGRESO"),
    ("HORAS_EXTRA", "Horas extra", "INGRESO"),
    ("COMISION_MANUAL", "Ajuste de comisión", "INGRESO"),
    ("ANTICIPO", "Anticipo", "DEDUCCION"),
    ("DESCUENTO", "Descuento autorizado", "DEDUCCION"),
    ("FALTA_NO_JUSTIFICADA", "Falta no justificada", "DEDUCCION"),
]

CATEGORIAS = ["Papelería", "Tecnología y accesorios", "Limpieza", "Cafetería"]


# Permisos por rol. Clave "app" = todas las tablas de la app; "app.modelo" = solo esa tabla.
# Acciones: view, add, change, delete.
_GESTION_BASES = {
    "ventas.basecomercial": ["view", "add", "change"],
    "ventas.asignacionbase": ["view", "add", "change"],
    "ventas.solicitudbase": ["view", "change"],
    "ventas.registrobase": ["view", "add", "change"],  # contactos de las bases (carga, reparto y seguimiento)
    "ventas.contactorepetido": ["view", "add", "change"],  # base de repetidos (revisar, descartar o recuperar)
}
# Catálogo de bancos y BIN para la validación de tarjetas (lo mantienen administración y cierre).
_CATALOGO_TARJETAS = {
    "ventas.entidademisora": ["view", "add", "change"],
    "ventas.rangobin": ["view", "add", "change", "delete"],
}
PERMISOS_POR_ROL = {
    Rol.ADMINISTRADOR: {
        app: ["view", "add", "change", "delete"]
        for app in ["personal", "documentos", "asistencia", "inventario", "agenda", "nomina", "reportes", "core"]
    } | {"cierre": ["view", "add", "change"]} | {
        # En ventas consulta y gestiona las bases comerciales; no entra al catálogo de tarjetas.
        f"ventas.{modelo}": ["view"]
        for modelo in ["cliente", "formulariodami", "historialestadoformulario", "tarjetadami", "hojaproduccion", "registroproduccion"]
    } | _GESTION_BASES,
    Rol.VENDEDOR: {
        "ventas": ["view"],
        "ventas.formulariodami": ["view", "add", "change"],
        "ventas.cliente": ["view", "add", "change"],
        "ventas.registroproduccion": ["view", "change"],  # escribe la aprobación en su hoja
        "ventas.solicitudbase": ["view", "add"],
        "ventas.registrobase": ["view", "change"],  # categoriza los clientes de sus bases
        "ventas.solicitudayuda": ["view", "add", "change"],  # pide ayuda a otro asesor con un contacto, o la da
        "agenda": ["view", "add", "change", "delete"],
    },
    Rol.ENCARGADO_CIERRE: {
        "ventas": ["view", "change"],
        "cierre": ["view", "add", "change"],
        "agenda": ["view", "add", "change", "delete"],
        "reportes": ["view"],
    } | _GESTION_BASES | _CATALOGO_TARJETAS,
    # TI: solo su módulo y sus reportes. No ve personal, nómina, ventas ni nada del administrador.
    Rol.TI: {
        "ti": ["view", "add", "change"],
        "reportes": ["view"],
    },
}


class Command(BaseCommand):
    help = "Carga catálogos iniciales y grupos de permisos de CALLFLOW."

    @transaction.atomic
    def handle(self, *args, **options):
        for nombre in CARGOS:
            Cargo.objects.get_or_create(nombre=nombre)

        for codigo, nombre, requerido in TIPOS_DOCUMENTO:
            TipoDocumento.objects.update_or_create(codigo=codigo, defaults={"nombre": nombre, "requerido_ingreso": requerido})

        for codigo, nombre, naturaleza in TIPOS_NOVEDAD:
            TipoNovedadNomina.objects.update_or_create(codigo=codigo, defaults={"nombre": nombre, "naturaleza": naturaleza})

        for nombre in CATEGORIAS:
            CategoriaMaterial.objects.get_or_create(nombre=nombre)

        Horario.objects.get_or_create(
            nombre="Jornada estándar",
            # 9:00 a 18:00 (settings.JORNADA_LABORAL) y sin tolerancia: las multas empiezan a las 9:00:01.
            defaults={"hora_entrada": jornada.inicio(), "hora_salida": jornada.fin(), "minutos_tolerancia": 0,
                      "dias_laborables": jornada.dias()},
        )

        for codigo, nombre, modulo, filtros, roles, descripcion in TIPOS_REPORTE:
            TipoReporte.objects.update_or_create(
                codigo=codigo,
                defaults={"nombre": nombre, "modulo": modulo, "filtros_disponibles": filtros, "roles_permitidos": list(roles),
                          "descripcion": descripcion},
            )

        for rol, apps in PERMISOS_POR_ROL.items():
            grupo, _ = Group.objects.get_or_create(name=rol.label)
            permisos = Permission.objects.none()
            for clave, acciones in apps.items():
                app, _, modelo = clave.partition(".")
                filtro = {"content_type__app_label": app} | ({"content_type__model": modelo} if modelo else {})
                codenames = [p.codename for p in Permission.objects.filter(**filtro) if p.codename.split("_", 1)[0] in acciones]
                permisos = permisos | Permission.objects.filter(**filtro, codename__in=codenames)
            grupo.permissions.set(permisos.distinct())

        # Cuentas de TI quemadas en el sistema: si alguien las borró de una base nueva, se reponen.
        for usuario, nombres, apellidos in CUENTAS_TI:
            cuenta, creada = Usuario.objects.get_or_create(username=usuario, defaults={
                "first_name": nombres, "last_name": apellidos, "rol": Rol.TI, "protegida": True})
            if creada:
                cuenta.set_unusable_password()  # el código se entrega con `python manage.py credenciales_ti`
                cuenta.save(update_fields=["password"])

        # Cada usuario en el grupo de su rol (las cuentas creadas por migraciones no pasan por la señal).
        for cuenta in Usuario.objects.all():
            grupo = Group.objects.filter(name=cuenta.get_rol_display()).first()
            if grupo and not cuenta.groups.filter(pk=grupo.pk).exists():
                cuenta.groups.add(grupo)

        if options["verbosity"] > 0:
            self.stdout.write(self.style.SUCCESS("Datos iniciales de CALLFLOW cargados."))
