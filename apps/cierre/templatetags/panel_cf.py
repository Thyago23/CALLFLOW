from django import template

from apps.ventas.templatetags.humanize_cf import dinero

register = template.Library()

OCULTO = "••••"


@register.filter
def monto(valor, ocultar=False):
    """SCRUM-58: con los valores ocultos, el monto no llega al navegador (no se esconde con CSS)."""
    if ocultar:
        return OCULTO
    if valor is None:
        return "—"
    return f"${dinero(valor)}"


@register.filter
def monto_entero(valor):
    """Para el tablero de la TV: '$1.234' (los centavos van aparte, más pequeños)."""
    return f"${dinero(valor or 0).split(',')[0]}"


@register.filter
def centavos(valor):
    return dinero(valor or 0).split(",")[1]
