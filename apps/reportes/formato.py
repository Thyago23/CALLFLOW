"""Formato de los valores de los reportes, igual en pantalla y en PDF."""
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from apps.ventas.templatetags.humanize_cf import dinero as _dinero

NUMERICOS = {"dinero", "entero", "cantidad", "porcentaje"}


def dinero(valor) -> str:
    """1234.5 -> '$1.234,50'; los descuentos: -40 -> '-$40,00'."""
    return f"-${_dinero(-valor)}" if valor < 0 else f"${_dinero(valor)}"


def entero(valor) -> str:
    return f"{int(valor):,}".replace(",", ".")


def cantidad(valor) -> str:
    """Stock: 12 -> '12', 1.50 -> '1,5'."""
    texto = format(Decimal(valor).normalize(), "f")
    entera, _, decimales = texto.partition(".")
    return entero(int(entera)) + (f",{decimales}" if decimales else "")


def porcentaje(valor) -> str:
    return f"{Decimal(valor):.0f} %"


def fecha(valor) -> str:
    if isinstance(valor, datetime):
        return timezone.localtime(valor).strftime("%d/%m/%Y %H:%M")
    return valor.strftime("%d/%m/%Y") if isinstance(valor, date) else str(valor)


def compacto(valor) -> str:
    """Para ejes de gráficas: 950 -> '$950', 12500 -> '$12,5K', 1200000 -> '$1,2M'."""
    valor = float(valor or 0)
    for limite, sufijo in ((1_000_000, "M"), (1_000, "K")):
        if abs(valor) >= limite:
            texto = f"{valor / limite:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            return f"${texto}{sufijo}"
    return f"${valor:.0f}"


_FORMATOS = {"dinero": dinero, "entero": entero, "cantidad": cantidad, "porcentaje": porcentaje, "fecha": fecha}


def formatear(valor, tipo: str = "texto", vacio: str = "—") -> str:
    if valor is None or valor == "":
        return vacio
    return _FORMATOS.get(tipo, str)(valor)
