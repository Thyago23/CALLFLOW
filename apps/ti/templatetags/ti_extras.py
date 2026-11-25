"""Filtros de plantilla de TI (duraciones y valores absolutos)."""

from datetime import timedelta

from django import template

register = template.Library()


@register.filter
def duracion(valor) -> str:
    """timedelta → «15 min», «2 h 30 min», «3 d 4 h». Vacío si no hay valor."""
    if not isinstance(valor, timedelta):
        return "—"
    minutos = int(valor.total_seconds() // 60)
    if minutos < 60:
        return f"{max(minutos, 0)} min"
    horas, minutos = divmod(minutos, 60)
    if horas < 24:
        return f"{horas} h" + (f" {minutos} min" if minutos else "")
    dias, horas = divmod(horas, 24)
    return f"{dias} d" + (f" {horas} h" if horas else "")


@register.filter
def absoluto(valor):
    try:
        return abs(valor)
    except TypeError:
        return valor
