"""
Prueba las reglas de integridad de la base de datos (restricciones y triggers).
Todo ocurre dentro de una transacción que se revierte al final: no deja datos.

    python manage.py verificar_bd
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.asistencia.models import NovedadAsistencia
from apps.cierre.models import GestionCierre, MetaMensual, ResumenVendedorMes
from apps.core.models import Notificacion, RegistroCambio, Rol, Usuario
from apps.documentos.models import Documento, TipoDocumento
from apps.inventario.models import CategoriaMaterial, Material, MovimientoInventario
from apps.nomina.models import LiquidacionNomina, NovedadNomina, PeriodoNomina, TipoNovedadNomina
from apps.personal.models import Cargo, Empleado, HistorialSalario
from apps.ventas.models import Cliente, FormularioDAMI, HistorialEstadoFormulario, RegistroProduccion


class _Revertir(Exception):
    pass


class Command(BaseCommand):
    help = "Verifica restricciones y triggers de la base de CALLFLOW (sin dejar datos)."

    def handle(self, *args, **options):
        self.ok = 0
        self.fallos = []
        try:
            with transaction.atomic():
                self._pruebas()
                raise _Revertir
        except _Revertir:
            pass
        total = self.ok + len(self.fallos)
        if self.fallos:
            for f in self.fallos:
                self.stderr.write(self.style.ERROR(f"  ✗ {f}"))
            raise CommandError(f"{len(self.fallos)} de {total} verificaciones fallaron.")
        self.stdout.write(self.style.SUCCESS(f"Las {total} verificaciones pasaron. (Se revirtieron todos los datos de prueba.)"))

    # ---------------------------------------------------------------- utilidades
    def cumple(self, descripcion, condicion):
        if condicion:
            self.ok += 1
            self.stdout.write(f"  ✓ {descripcion}")
        else:
            self.fallos.append(descripcion)
            self.stdout.write(self.style.ERROR(f"  ✗ {descripcion}"))

    def rechaza(self, descripcion, funcion):
        try:
            with transaction.atomic():
                funcion()
        except DatabaseError as e:
            self.ok += 1
            motivo = str(e).strip().splitlines()[0][:110]
            self.stdout.write(f"  ✓ {descripcion}  → {motivo}")
            return
        self.fallos.append(f"{descripcion} (la base lo permitió)")
        self.stdout.write(self.style.ERROR(f"  ✗ {descripcion} (la base lo permitió)"))

    # ---------------------------------------------------------------- escenarios
    def _pruebas(self):
        hoy = date.today()
        ahora = timezone.now()

        admin = Usuario.objects.create_user("verif_admin", "verif_admin@callflow.test", "x", rol=Rol.ADMINISTRADOR)
        with connection.cursor() as c:
            c.execute("SELECT set_config('callflow.usuario_id', %s, true)", [str(admin.pk)])

        cargo = Cargo.objects.create(nombre="Cargo verificación")
        vendedor = Empleado.objects.create(identificacion="V-0001", nombres="Ana", apellidos="Vendedora", cargo=cargo, fecha_ingreso=hoy)
        encargado = Empleado.objects.create(identificacion="E-0001", nombres="Luis", apellidos="Cierre", cargo=cargo, fecha_ingreso=hoy)

        self.stdout.write("\n4.1 Gestión del personal")
        self.rechaza("Identificación de empleado duplicada", lambda: Empleado.objects.create(
            identificacion="V-0001", nombres="X", apellidos="Y", cargo=cargo, fecha_ingreso=hoy))
        self.rechaza("Fecha de salida anterior al ingreso", lambda: Empleado.objects.create(
            identificacion="V-0002", nombres="X", apellidos="Y", cargo=cargo, fecha_ingreso=hoy, fecha_salida=hoy - timedelta(days=1)))
        HistorialSalario.objects.create(empleado=vendedor, salario_base=Decimal("470.00"), vigente_desde=hoy)
        self.rechaza("Dos salarios vigentes a la vez para el mismo empleado", lambda: HistorialSalario.objects.create(
            empleado=vendedor, salario_base=Decimal("500.00"), vigente_desde=hoy + timedelta(days=10)))
        self.rechaza("Salario en cero", lambda: HistorialSalario.objects.create(
            empleado=encargado, salario_base=0, vigente_desde=hoy))
        self.cumple("El cambio de salario quedó en la bitácora de auditoría",
                    RegistroCambio.objects.filter(tabla="personal_historialsalario", usuario_id=admin.pk).exists())
        self.rechaza("La bitácora de auditoría no se puede borrar", lambda: RegistroCambio.objects.all().delete())

        self.stdout.write("\n4.1 Gestión documental")
        tipo_doc = TipoDocumento.objects.get_or_create(codigo="VERIF", defaults={"nombre": "Verificación"})[0]
        self.rechaza("Archivo con formato no permitido (.exe)", lambda: Documento.objects.create(
            tipo=tipo_doc, archivo="x.exe", nombre_original="x.exe", tipo_mime="application/x-msdownload",
            tamano_bytes=10, hash_sha256="a" * 64))
        self.rechaza("Archivo de más de 10 MB", lambda: Documento.objects.create(
            tipo=tipo_doc, archivo="x.pdf", nombre_original="x.pdf", tipo_mime="application/pdf",
            tamano_bytes=11 * 1024 * 1024, hash_sha256="b" * 64))

        self.stdout.write("\n4.1 Asistencia")
        self.rechaza("Atraso registrado sin minutos", lambda: NovedadAsistencia.objects.create(
            empleado=vendedor, fecha=hoy, tipo="ATRASO"))

        self.stdout.write("\n4.1 Inventario de suministros")
        categoria = CategoriaMaterial.objects.create(nombre="Categoría verificación")
        material = Material.objects.create(codigo="VERIF-1", nombre="Resma verificación", categoria=categoria, stock_minimo=5)
        MovimientoInventario.objects.create(material=material, tipo="ENTRADA", cantidad=10)
        MovimientoInventario.objects.create(material=material, tipo="SALIDA", cantidad=3)
        material.refresh_from_db()
        self.cumple("El stock se calcula solo con los movimientos (10 − 3 = 7)", material.stock_actual == 7)
        self.rechaza("Salida mayor al stock disponible", lambda: MovimientoInventario.objects.create(
            material=material, tipo="SALIDA", cantidad=100))
        self.rechaza("Modificar un movimiento ya registrado", lambda: MovimientoInventario.objects.filter(
            material=material).update(cantidad=1))
        Material.objects.filter(pk=material.pk).update(stock_actual=999)
        material.refresh_from_db()
        self.cumple("Editar el stock directamente no tiene efecto", material.stock_actual == 7)
        MovimientoInventario.objects.create(material=material, tipo="SALIDA", cantidad=3)
        self.cumple("Al llegar al mínimo se notifica a los administradores",
                    Notificacion.objects.filter(destinatario=admin, tipo="STOCK_BAJO").exists())

        self.stdout.write("\n4.2 Nómina")
        inicio = date(2099, 1, 1)
        periodo = PeriodoNomina.objects.create(nombre="Verificación 2099-01", fecha_inicio=inicio, fecha_fin=date(2099, 1, 31))
        self.rechaza("Dos periodos de nómina que se solapan", lambda: PeriodoNomina.objects.create(
            nombre="Solapado", fecha_inicio=date(2099, 1, 15), fecha_fin=date(2099, 2, 14)))
        self.rechaza("Liquidación cuyo neto no cuadra", lambda: LiquidacionNomina.objects.create(
            periodo=periodo, empleado=vendedor, total_ingresos=500, total_deducciones=50, neto=999))
        tipo_nov = TipoNovedadNomina.objects.get_or_create(codigo="VERIF", defaults={"nombre": "Verif", "naturaleza": "INGRESO"})[0]
        NovedadNomina.objects.create(empleado=vendedor, periodo=periodo, tipo=tipo_nov, valor=20)
        PeriodoNomina.objects.filter(pk=periodo.pk).update(estado="CERRADO", aprobado_en=ahora, cerrado_en=ahora)
        self.rechaza("Agregar una novedad a un periodo cerrado", lambda: NovedadNomina.objects.create(
            empleado=vendedor, periodo=periodo, tipo=tipo_nov, valor=10))
        self.rechaza("Reabrir o editar un periodo cerrado", lambda: PeriodoNomina.objects.filter(
            pk=periodo.pk).update(estado="ABIERTO"))

        self.stdout.write("\n4.3 Ventas")
        cliente = Cliente.objects.create(identificacion="C-0001", nombres="Carla", apellidos="Cliente", telefono="0999999999")
        dami = FormularioDAMI.objects.create(vendedor=vendedor, cliente=cliente, tiene_tarjeta_activa=True,
                                             estado="ENVIADO", enviado_en=ahora)
        dami.refresh_from_db()
        self.cumple("Un DAMI con tarjeta activa queda como prioritario", dami.prioridad == 1)
        self.cumple("Al enviar el DAMI se crea su fila en la hoja de producción",
                    RegistroProduccion.objects.filter(formulario=dami, estado="PENDIENTE").exists())
        self.rechaza("Marcar un DAMI como venta cerrada sin gestión de cierre", lambda: FormularioDAMI.objects.filter(
            pk=dami.pk).update(estado="VENTA_CERRADA"))
        self.rechaza("Crear un DAMI directamente como cerrado", lambda: FormularioDAMI.objects.create(
            vendedor=vendedor, cliente=cliente, estado="VENTA_CERRADA", enviado_en=ahora))
        self.rechaza("Hoja de producción con venta cerrada sin valor", lambda: RegistroProduccion.objects.filter(
            formulario=dami).update(estado="VENTA_CERRADA"))
        self.rechaza("Cliente duplicado", lambda: Cliente.objects.create(
            identificacion="C-0001", nombres="Otra", apellidos="Persona", telefono="1"))

        self.stdout.write("\n4.4 Cierre de ventas")
        self.rechaza("Un vendedor cierra su propio DAMI", lambda: GestionCierre.objects.create(
            formulario=dami, encargado=vendedor, resultado="VENTA_CERRADA", valor_venta=100))
        self.rechaza("Venta cerrada sin valor", lambda: GestionCierre.objects.create(
            formulario=dami, encargado=encargado, resultado="VENTA_CERRADA"))
        version_antes = dami.version
        cambios = FormularioDAMI.objects.filter(pk=dami.pk, version=version_antes).update(estado="EN_GESTION")
        conflicto = FormularioDAMI.objects.filter(pk=dami.pk, version=version_antes).update(observaciones="otro encargado")
        self.cumple("Bloqueo optimista: un segundo guardado con versión vieja no sobrescribe", cambios == 1 and conflicto == 0)
        GestionCierre.objects.create(formulario=dami, encargado=encargado, resultado="VENTA_CERRADA", valor_venta=Decimal("150.00"))
        dami.refresh_from_db()
        registro = RegistroProduccion.objects.get(formulario=dami)
        self.cumple("La gestión cierra el DAMI y actualiza la hoja de producción",
                    dami.estado == "VENTA_CERRADA" and registro.estado == "VENTA_CERRADA" and registro.valor_venta == 150)
        self.cumple("Cada cambio de estado del DAMI queda en su historial",
                    HistorialEstadoFormulario.objects.filter(formulario=dami).count() == 3)
        self.rechaza("Eliminar una gestión de cierre", lambda: GestionCierre.objects.filter(formulario=dami).delete())
        MetaMensual.objects.create(vendedor=vendedor, anio=registro.fecha.year, mes=registro.fecha.month, monto_meta=Decimal("1000.00"))
        resumen = ResumenVendedorMes.objects.get(vendedor=vendedor, anio=registro.fecha.year, mes=registro.fecha.month)
        self.cumple("Panel: total vendido, pendiente de meta y ranking",
                    resumen.total_vendido == 150 and resumen.pendiente_meta == 850 and resumen.posicion == 1)
