"""
Importa la hoja INVENTARIO del Excel «Multivisas_Sistema_Control_Integral» como activos fijos de una empresa.

    python manage.py importar_activos_excel ruta/al/archivo.xlsx --empresa MULTIVISAS

No duplica: si ya hay un activo con la misma descripción en esa empresa, lo salta.
Las filas sin cantidad o sin valor (p. ej. «Cámara de seguridad») se importan con 1 y $0 para completarlas después.
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.contabilidad import servicios
from apps.contabilidad.models import ActivoFijo, Empresa


def _decimal(valor) -> Decimal:
    try:
        return Decimal(str(valor)).quantize(Decimal("0.01")) if valor not in (None, "") else Decimal("0.00")
    except InvalidOperation:
        return Decimal("0.00")


class Command(BaseCommand):
    help = "Importa los activos fijos de la hoja INVENTARIO del Excel de control integral."

    def add_arguments(self, parser):
        parser.add_argument("archivo")
        parser.add_argument("--empresa", required=True, help="Código de la empresa: MULTIVISAS o TYT.")
        parser.add_argument("--hoja", default="INVENTARIO")

    @transaction.atomic
    def handle(self, archivo, empresa, hoja, **opciones):
        import openpyxl

        destino = Empresa.objects.filter(codigo=empresa.upper()).first()
        if destino is None:
            raise CommandError(f"No existe la empresa {empresa}. Use uno de: "
                               + ", ".join(Empresa.objects.values_list("codigo", flat=True)))
        libro = openpyxl.load_workbook(archivo, read_only=True, data_only=True)
        if hoja not in libro.sheetnames:
            raise CommandError(f"El archivo no tiene la hoja {hoja}.")

        creados = saltados = 0
        encabezado = False
        for fila in libro[hoja].iter_rows(values_only=True):
            celdas = list(fila) + [None] * 5
            if not encabezado:
                encabezado = str(celdas[1] or "").strip().lower().startswith("descrip")
                continue
            descripcion = " ".join(str(celdas[1] or "").split())
            if not descripcion or str(celdas[0] or "").strip().upper() == "TOTAL":
                continue
            if ActivoFijo.objects.filter(empresa=destino, descripcion__iexact=descripcion).exists():
                saltados += 1
                continue
            cantidad = int(celdas[2]) if isinstance(celdas[2], (int, float)) and celdas[2] > 0 else 1
            activo = ActivoFijo(empresa=destino, descripcion=descripcion[:150], cantidad=cantidad,
                                valor_unitario=_decimal(celdas[3]),
                                observaciones="Importado del Excel de control integral (hoja INVENTARIO).")
            servicios.guardar_con_codigo(activo, "AF")
            creados += 1
        self.stdout.write(self.style.SUCCESS(f"{destino}: {creados} activos importados, {saltados} ya existían."))
