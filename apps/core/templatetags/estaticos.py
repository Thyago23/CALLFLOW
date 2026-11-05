"""
{% estatico 'css/callflow.css' %} -> /static/css/callflow.css?v=1730835916

Agrega la fecha de modificación del archivo a la URL: cuando el archivo cambia, cambia la URL
y el navegador descarga la versión nueva en vez de usar la copia vieja de su caché.
"""
from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def estatico(ruta: str) -> str:
    url = static(ruta)
    archivo = finders.find(ruta)
    if not archivo:
        return url
    from pathlib import Path

    return f"{url}?v={int(Path(archivo).stat().st_mtime)}"
