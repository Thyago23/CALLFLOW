from datetime import timedelta

from django import template

register = template.Library()


@register.filter
def duracion(valor):
    """timedelta -> '8 h 15 min'."""
    if not isinstance(valor, timedelta):
        return "—"
    minutos = int(valor.total_seconds() // 60)
    horas, resto = divmod(minutos, 60)
    if horas and resto:
        return f"{horas} h {resto} min"
    return f"{horas} h" if horas else f"{resto} min"


@register.filter
def minutos_a_texto(minutos):
    """95 -> '1 h 35 min'."""
    if not minutos:
        return "0 min"
    return duracion(timedelta(minutes=int(minutos)))
