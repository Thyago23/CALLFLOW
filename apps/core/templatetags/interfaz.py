from django import template
from django.utils import timezone
from django.utils.timesince import timesince

register = template.Library()


@register.filter
def hace(fecha) -> str:
    """'hace 17 horas' en vez de '17 horas, 51 minutos'."""
    if not fecha:
        return ""
    if (timezone.now() - fecha).total_seconds() < 60:
        return "ahora mismo"  # en vez de «hace 0 minutos»
    return f"hace {timesince(fecha).split(',')[0]}"
