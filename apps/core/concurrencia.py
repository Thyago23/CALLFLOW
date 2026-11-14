"""
Concurrencia optimista: evita que dos personas que editan el mismo registro a la vez se pisen los cambios.

Cómo funciona (sin columnas nuevas en la base, para no tener que migrar la base compartida):
1. Al abrir el formulario de edición se calcula la «huella» del registro: un resumen de cada campo que el formulario
   edita, tal como está en la base en ese momento. Va en un campo oculto, firmado con SECRET_KEY.
2. Al guardar, el servidor bloquea la fila (SELECT … FOR UPDATE), vuelve a calcular la huella con lo que hay en la base
   y la compara con la que trajo el formulario.
3. Si alguien guardó entre medio, no se guarda nada: el formulario vuelve con un aviso que dice qué campos cambió la
   otra persona y su valor actual. Si la persona vuelve a guardar después de revisarlo, se guarda (ya vio el cambio).

Solo cuentan los campos que el formulario edita: si otra persona cambió un campo que este formulario no muestra,
no hay conflicto. El formulario DAMI usa su propia columna `version` (ver apps.ventas.servicios.guardar_formulario).

Uso: `class ChipForm(ControlConcurrencia, FormularioEstilizado, forms.ModelForm)` y en la plantilla
`{{ form.huella_registro }}` dentro del <form>.
"""
import hashlib

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction

CAMPO = "huella_registro"
_SAL = "callflow.concurrencia"


def _valor(instancia, nombre: str):
    campo = instancia._meta.get_field(nombre)
    if campo.many_to_many:
        if not instancia.pk:
            return []
        return sorted(getattr(instancia, nombre).values_list("pk", flat=True))
    valor = campo.value_from_object(instancia)
    return getattr(valor, "name", valor)  # archivos: la ruta guardada


def _resumen(valor) -> str:
    return hashlib.sha256(repr(valor).encode("utf-8")).hexdigest()[:10]


def _mostrar(instancia, nombre: str) -> str:
    campo = instancia._meta.get_field(nombre)
    if campo.many_to_many:
        texto = ", ".join(str(o) for o in getattr(instancia, nombre).all())
    elif campo.choices:
        texto = getattr(instancia, f"get_{nombre}_display")()
    elif campo.is_relation:
        texto = str(getattr(instancia, nombre) or "")
    else:
        texto = str(campo.value_from_object(instancia) or "")
    texto = texto or "vacío"
    return texto if len(texto) <= 60 else texto[:57] + "…"


class ControlConcurrencia:
    """Mixin para ModelForm de edición. En formularios de alta (sin pk) no hace nada."""

    mensaje_conflicto = "Otra persona modificó este registro mientras usted lo editaba."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # No es «required»: el error de un campo oculto no se ve en pantalla; se avisa como error general en clean().
            self.fields[CAMPO] = forms.CharField(widget=forms.HiddenInput, required=False, initial=self._firmar(self.instance))

    # ------------------------------------------------------------------ huella
    def _campos_controlados(self) -> list[str]:
        propios = {f.name for f in self.instance._meta.get_fields() if getattr(f, "concrete", False) or f.many_to_many}
        return [n for n in self.fields if n != CAMPO and n in propios]

    def _huellas(self, instancia) -> dict:
        return {n: _resumen(_valor(instancia, n)) for n in self._campos_controlados()}

    def _firmar(self, instancia) -> str:
        return signing.dumps(self._huellas(instancia), salt=_SAL, compress=True)

    # ------------------------------------------------------------------ validación
    def clean(self):
        datos = super().clean()
        if not self.instance.pk or CAMPO not in self.fields:
            return datos
        token = self.cleaned_data.get(CAMPO)
        try:
            if not token:
                raise signing.BadSignature
            originales = signing.loads(token, salt=_SAL)
        except signing.BadSignature:
            raise ValidationError("Recargue la página e intente de nuevo.", code="huella_invalida") from None

        modelo = type(self.instance)
        with transaction.atomic():  # con ATOMIC_REQUESTS el bloqueo dura hasta el final de la petición
            actual = modelo._default_manager.select_for_update().filter(pk=self.instance.pk).first()
        if actual is None:
            raise ValidationError("Este registro fue eliminado por otra persona.", code="eliminado")

        huellas = self._huellas(actual)
        cambiados = [n for n, h in huellas.items() if originales.get(n) != h]
        if cambiados:
            # La próxima vez que guarde ya habrá visto el cambio: se le entrega la huella nueva.
            self.data = self.data.copy()
            self.data[self.add_prefix(CAMPO)] = self._firmar(actual)
            detalle = "; ".join(f"{self.fields[n].label}: «{_mostrar(actual, n)}»" for n in cambiados)
            raise ValidationError(
                f"{self.mensaje_conflicto} Cambió: {detalle}. Revise sus datos y, si quiere mantenerlos, guarde de nuevo.",
                code="conflicto")
        return datos


def huella_actual(form_class, instancia, **kwargs) -> dict:
    """{campo oculto: huella} del registro tal como está ahora. Para pruebas y scripts que envían el formulario."""
    return {CAMPO: form_class(instance=instancia, **kwargs)[CAMPO].value()}
