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
