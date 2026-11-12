"""Vista previa del contrato en HTML con el mismo formato que el PDF (títulos y negritas)."""
import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.documentos import contratos

register = template.Library()


def _html(texto: str) -> str:
    """Escapa primero (el texto lo escriben usuarios) y después convierte **negrita**."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(texto), flags=re.S).replace("\n", "<br>")


@register.filter
def formato_contrato(texto: str) -> str:
    partes = []
    for tipo, valor in contratos.bloques(texto):
        if tipo == "titulo":
            partes.append('<h3 class="titulo-contrato">' + "<br>".join(_html(l) for l in valor) + "</h3>")
        elif tipo == "clausula":
            partes.append(f'<h4 class="clausula-contrato">{_html(valor)}</h4>')
        elif tipo == "numeral":
            numero, cuerpo = valor
            letra = " letra" if numero[0].isalpha() else ""
            partes.append(f'<p class="numeral-contrato{letra}"><span class="numero">{escape(numero)}</span><span>{_html(cuerpo)}</span></p>')
        elif tipo == "tabla":
            filas = []
            for i, fila in enumerate(valor):
                etiqueta = "th" if i == 0 else "td"
                filas.append("<tr>" + "".join(f"<{etiqueta}>{_html(c)}</{etiqueta}>" for c in fila) + "</tr>")
            partes.append('<table class="tabla-contrato">' + "".join(filas) + "</table>")
        elif tipo == "espacio":
            partes.append('<div class="espacio-contrato" aria-hidden="true"></div>')
        elif tipo == "firma":
            partes.append(f'<p class="firma-contrato">{_html(valor)}</p>')
        else:
            partes.append(f"<p>{_html(valor)}</p>")
    return mark_safe("\n".join(partes))
