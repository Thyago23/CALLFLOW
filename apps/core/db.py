"""Utilidades de base de datos compartidas entre módulos."""
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeBoundary, RangeOperators
from django.db.models import Func


class DateRange(Func):
    """daterange(desde, hasta, '[]') de PostgreSQL. Un 'hasta' nulo significa vigencia indefinida."""

    function = "daterange"
    output_field = DateRangeField()


def sin_solapamiento(nombre: str, desde: str, hasta: str, *por: str) -> ExclusionConstraint:
    """
    Impide que dos periodos de vigencia se solapen (por ejemplo, dos salarios vigentes
    a la vez para el mismo empleado). Requiere la extensión btree_gist cuando se usa `por`.
    """
    expresiones = [(DateRange(desde, hasta, RangeBoundary(inclusive_lower=True, inclusive_upper=True)), RangeOperators.OVERLAPS)]
    expresiones += [(campo, RangeOperators.EQUAL) for campo in por]
    return ExclusionConstraint(name=nombre, expressions=expresiones)
