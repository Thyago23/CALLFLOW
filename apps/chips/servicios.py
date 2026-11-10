"""Reglas del registro de chips: la reposición y el cambio de número quedan siempre registrados."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum

from .models import Chip, Reposicion

FIRMAS_POR_DEFECTO = [
    ("Raul Tapia", "Dueño"),
    ("Diego Mendoza", "Gerente de Talento Humano y Marketing"),
    ("Marlon Rosero de Mendoza", "Coordinador"),
]


def firmas_reporte() -> list[tuple[str, str]]:
    """Quiénes firman el reporte de reposiciones (se cambia en settings.CHIPS_FIRMAS_REPORTE)."""
    return getattr(settings, "CHIPS_FIRMAS_REPORTE", FIRMAS_POR_DEFECTO)


@transaction.atomic
def registrar_reposicion(chip: Chip, *, fecha: date, motivo: str, numero_nuevo: str = "", iccid_nuevo: str = "",
                         operadora: str = "", costo: Decimal | None = None, observacion: str = "", usuario=None) -> Reposicion:
    """
    Guarda la reposición con el número y la serie de antes y de después, y actualiza el chip.
    Si cambia el número, el chip pasa a tener el número nuevo (el anterior queda en el historial).
    """
    chip = Chip.objects.select_for_update().get(pk=chip.pk)
    if chip.estado == Chip.Estado.BAJA:
        raise ValidationError("El chip está dado de baja: no se le pueden registrar reposiciones.")
    numero_nuevo = (numero_nuevo or "").strip() or chip.numero
    if motivo == Reposicion.Motivo.CAMBIO_NUMERO and numero_nuevo == chip.numero:
        raise ValidationError({"numero_nuevo": "Para un cambio de número escriba el número nuevo (distinto del actual)."})
    if numero_nuevo != chip.numero and Chip.objects.filter(numero=numero_nuevo).exclude(pk=chip.pk).exists():
        raise ValidationError({"numero_nuevo": f"El número {numero_nuevo} ya está registrado en otro chip."})

    reposicion = Reposicion(
        chip=chip, fecha=fecha, motivo=motivo, numero_anterior=chip.numero, numero_nuevo=numero_nuevo,
        iccid_anterior=chip.iccid, iccid_nuevo=(iccid_nuevo or "").strip() or chip.iccid,
        operadora=operadora or chip.operadora, costo=costo, observacion=observacion.strip(), registrado_por=usuario,
    )
    reposicion.full_clean()
    reposicion.save()

    chip.numero, chip.iccid, chip.operadora = reposicion.numero_nuevo, reposicion.iccid_nuevo, reposicion.operadora
    chip.estado = Chip.Estado.ACTIVO  # el chip repuesto queda en uso
    chip.save(update_fields=["numero", "iccid", "operadora", "estado", "actualizado_en"])
    return reposicion


@dataclass
class ReporteReposiciones:
    desde: date
    hasta: date
    reposiciones: list
    total: int
    con_cambio_numero: int
    costo_total: Decimal
    firmas: list

    @property
    def mismo_numero(self) -> int:
        return self.total - self.con_cambio_numero


def reporte_reposiciones(desde: date, hasta: date, operadora: str = "") -> ReporteReposiciones:
    qs = (Reposicion.objects.filter(fecha__range=(desde, hasta))
          .select_related("chip__asignado_a", "registrado_por").order_by("fecha", "pk"))
    if operadora:
        qs = qs.filter(operadora=operadora)
    filas = list(qs)
    return ReporteReposiciones(
        desde=desde, hasta=hasta, reposiciones=filas, total=len(filas),
        con_cambio_numero=sum(1 for r in filas if r.cambio_numero),
        costo_total=qs.aggregate(t=Sum("costo"))["t"] or Decimal("0"),
        firmas=firmas_reporte(),
    )


def resumen(desde: date, hasta: date) -> dict:
    del_mes = Reposicion.objects.filter(fecha__range=(desde, hasta))
    return {
        "activos": Chip.objects.filter(estado=Chip.Estado.ACTIVO).count(),
        "total": Chip.objects.exclude(estado=Chip.Estado.BAJA).count(),
        "reposiciones_mes": del_mes.count(),
        "cambios_mes": del_mes.exclude(numero_nuevo=F("numero_anterior")).count(),
    }
