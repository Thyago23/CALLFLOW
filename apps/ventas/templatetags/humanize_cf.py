from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def hora_aprobacion(valor):
    """'0918' -> '09:18'. Valores antiguos que no son una hora se muestran tal cual."""
    texto = str(valor or "")
    if len(texto) == 4 and texto.isdigit() and int(texto[:2]) < 24 and int(texto[2:]) < 60:
        return f"{texto[:2]}:{texto[2:]}"
    return texto


@register.filter
def dinero(valor):
    """1234.5 -> '1.234,50' (formato de Ecuador)."""
    try:
        numero = Decimal(valor or 0).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return valor
    entero, decimales = f"{numero:,.2f}".split(".")
    return f"{entero.replace(',', '.')},{decimales}"
