from django import template
from django.utils.timesince import timesince

register = template.Library()


@register.filter
def hace(fecha) -> str:
    """'hace 17 horas' en vez de '17 horas, 51 minutos'."""
    if not fecha:
        return ""
    return f"hace {timesince(fecha).split(',')[0]}"
