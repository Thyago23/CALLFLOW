"""
Inventario de suministros (SCRUM-25 a SCRUM-27).

El stock lo calcula la base de datos con cada movimiento (trigger inventario_aplicar_movimiento):
aquí solo se valida antes para dar mensajes claros. La base sigue siendo la última barrera:
rechaza salidas mayores al stock, materiales inactivos y la edición de movimientos.
"""
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from .models import CategoriaMaterial, Material, MovimientoInventario

Tipo = MovimientoInventario.Tipo
TIPOS_QUE_SUMAN = {Tipo.ENTRADA, Tipo.AJUSTE_POSITIVO}
TIPOS_CON_MOTIVO = {Tipo.AJUSTE_POSITIVO, Tipo.AJUSTE_NEGATIVO}


def materiales_stock_bajo():
    return Material.objects.filter(activo=True, stock_actual__lte=F("stock_minimo"))


def siguiente_codigo() -> str:
    """MAT-0001, MAT-0002… (el bloqueo evita que dos personas reciban el mismo código)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('codigo_material'))")
    numeros = [int(m.group(1)) for c in Material.objects.filter(codigo__startswith="MAT-").values_list("codigo", flat=True)
               if (m := re.fullmatch(r"MAT-(\d+)", c))]
    return f"MAT-{max(numeros, default=0) + 1:04d}"


def categoria_por_nombre(nombre: str) -> CategoriaMaterial:
    nombre = " ".join((nombre or "").split())
    existente = CategoriaMaterial.objects.filter(nombre__iexact=nombre).first()
    return existente or CategoriaMaterial.objects.create(nombre=nombre[:80])


def _mensaje_base_de_datos(error: DatabaseError) -> str:
    """El texto del RAISE EXCEPTION del trigger, sin el contexto técnico."""
    return str(error).strip().splitlines()[0]


@transaction.atomic
def crear_material(material: Material, stock_inicial: Decimal | None, usuario=None) -> Material:
    if not material.codigo:
        material.codigo = siguiente_codigo()
    material.save()
    if stock_inicial:
        registrar_movimiento(material, Tipo.ENTRADA, stock_inicial, "Stock inicial", usuario)
        material.refresh_from_db()
    return material


@transaction.atomic
def registrar_movimiento(material: Material, tipo: str, cantidad: Decimal, motivo: str = "", usuario=None) -> MovimientoInventario:
    motivo = (motivo or "").strip()
    if tipo not in Tipo.values:
        raise ValidationError("Tipo de movimiento no válido.")
    if cantidad is None or cantidad <= 0:
        raise ValidationError("La cantidad debe ser mayor que cero.")
    if tipo in TIPOS_CON_MOTIVO and not motivo:
        raise ValidationError("Los ajustes deben indicar el motivo (ej.: conteo físico, material dañado).")

    actual = Material.objects.select_for_update().get(pk=material.pk)
    if not actual.activo:
        raise ValidationError(f"El material «{actual.nombre}» está inactivo y no admite movimientos.")
    if tipo not in TIPOS_QUE_SUMAN and cantidad > actual.stock_actual:
        raise ValidationError(
            f"Stock insuficiente de «{actual.nombre}»: hay {actual.stock_actual.normalize():f} "
            f"{actual.get_unidad_medida_display().lower()} y se quieren sacar {cantidad.normalize():f}."
        )
    try:
        with transaction.atomic():
            movimiento = MovimientoInventario.objects.create(
                material=actual, tipo=tipo, cantidad=cantidad, motivo=motivo[:255], responsable=usuario
            )
    except DatabaseError as error:  # la base rechazó el movimiento (otra persona sacó stock a la vez, etc.)
        raise ValidationError(_mensaje_base_de_datos(error)) from error
    movimiento.refresh_from_db()  # stock_resultante lo calcula el trigger
    material.refresh_from_db()
    return movimiento


@transaction.atomic
def cambiar_estado(material: Material) -> Material:
    material.activo = not material.activo
    material.save(update_fields=["activo", "actualizado_en"])
    return material
