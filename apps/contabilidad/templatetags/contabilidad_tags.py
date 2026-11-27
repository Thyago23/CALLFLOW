from django import forms, template

from ..empresa import empresas

register = template.Library()


@register.filter
def campo(form, nombre):
    """{{ form|campo:"nombre" }}: el campo por su nombre (para el formulario genérico)."""
    return form[nombre] if nombre in form.fields else ""


@register.filter
def es_casilla(campo_ligado) -> bool:
    return isinstance(getattr(campo_ligado, "field", None) and campo_ligado.field.widget, forms.CheckboxInput)


@register.simple_tag
def empresas_activas():
    return empresas()
