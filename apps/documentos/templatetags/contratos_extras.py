"""Vista previa del contrato en HTML con el mismo formato que el PDF (títulos y negritas)."""
import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def formato_contrato(texto: str) -> str:
    bloques = []
    for bloque in re.split(r"\n\s*\n", (texto or "").strip()):
        bloque = escape(bloque.strip())  # primero se escapa todo: el texto lo escriben usuarios
        if not bloque:
            continue
        bloque = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", bloque, flags=re.S)
        if bloque.startswith("# "):
            bloques.append(f'<h3 class="titulo-contrato">{bloque[2:]}</h3>')
        else:
            clase = ' class="firma-contrato"' if bloque.startswith("___") else ""
            bloques.append(f"<p{clase}>{bloque.replace(chr(10), '<br>')}</p>")
    return mark_safe("\n".join(bloques))
