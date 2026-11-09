"""Selector de hora limitado a la jornada laboral (el <input type="time"> del navegador muestra las 24 horas)."""
from datetime import time

from django import forms

from apps.core import jornada

PASO_MINUTOS = 5


def _a_hora(valor) -> time | None:
    if isinstance(valor, time):
        return valor
    try:
        h, m = str(valor or "").split(":")[:2]
        return time(int(h), int(m))
    except (TypeError, ValueError):
        return None


class HoraLaboralWidget(forms.Widget):
    """
    Dos listas (hora y minutos, de 5 en 5) que solo ofrecen horas dentro de la jornada laboral.
    Con `incluir_cierre=True` se puede elegir la hora exacta de cierre (ej.: 18:00, para «Hasta»).
    Envía `<name>_h` y `<name>_m`; también acepta `<name>` como «HH:MM».
    """

    template_name = "agenda/widgets/hora_laboral.html"

    def __init__(self, attrs=None, incluir_cierre=False, opcional=True):
        super().__init__(attrs)
        self.incluir_cierre = incluir_cierre
        self.opcional = opcional

    def _horas(self):
        inicio, fin = jornada.inicio(), jornada.fin()
        ultima = fin.hour if (self.incluir_cierre or fin.minute > 0) else fin.hour - 1
        return list(range(inicio.hour, ultima + 1))

    def get_context(self, name, value, attrs):
        contexto = super().get_context(name, value, attrs)
        hora = _a_hora(value)
        minutos = list(range(0, 60, PASO_MINUTOS))
        if hora is not None and hora.minute not in minutos:  # eventos antiguos con minutos sueltos
            minutos = sorted(minutos + [hora.minute])
        contexto["widget"].update({
            "horas": [f"{h:02d}" for h in self._horas()],
            "minutos": [f"{m:02d}" for m in minutos],
            "hora_sel": f"{hora.hour:02d}" if hora else "",
            "minuto_sel": f"{hora.minute:02d}" if hora else "",
            "opcional": self.opcional,
            "inicio": f"{jornada.inicio():%H:%M}", "fin": f"{jornada.fin():%H:%M}",
            "incluir_cierre": self.incluir_cierre,
        })
        return contexto

    def value_from_datadict(self, data, files, name):
        h = data.get(f"{name}_h")
        if h:
            return f"{h}:{data.get(f'{name}_m') or '00'}"
        if f"{name}_h" in data:  # eligió «--»: sin hora
            return ""
        return data.get(name)

    def value_omitted_from_data(self, data, files, name):
        return not any(k in data for k in (name, f"{name}_h", f"{name}_m"))

    def id_for_label(self, id_):
        return f"{id_}_h" if id_ else id_
