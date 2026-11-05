from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def dinero(valor):
    """1234.5 -> '1.234,50' (formato de Ecuador)."""
    try:
        numero = Decimal(valor or 0).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return valor
    entero, decimales = f"{numero:,.2f}".split(".")
    return f"{entero.replace(',', '.')},{decimales}"
