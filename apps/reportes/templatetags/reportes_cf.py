from django import template

from apps.reportes import formato

register = template.Library()


@register.filter
def usd(valor):
    """1234.5 -> '$1.234,50'; vacío -> '—'."""
    return formato.formatear(valor, "dinero")


@register.filter
def pct(valor):
    return formato.formatear(valor, "porcentaje")


@register.filter
def compacto(valor):
    return formato.compacto(valor)
