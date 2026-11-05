"""
Jornada laboral de la empresa (settings.JORNADA_LABORAL).

    from apps.core import jornada
    jornada.inicio()        -> time(9, 0)
    jornada.fin()           -> time(18, 0)
    jornada.es_laborable(d) -> True de lunes a viernes
"""
from datetime import date, time

from django.conf import settings

_NOMBRES_DIAS = {1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves", 5: "viernes", 6: "sábado", 7: "domingo"}


def _hora(texto: str) -> time:
    horas, minutos = texto.split(":")
    return time(int(horas), int(minutos))


def inicio() -> time:
    return _hora(settings.JORNADA_LABORAL["inicio"])


def fin() -> time:
    return _hora(settings.JORNADA_LABORAL["fin"])


def dias() -> list[int]:
    return list(settings.JORNADA_LABORAL["dias"])


def es_laborable(dia: date) -> bool:
    return dia.isoweekday() in dias()


def dentro(hora: time) -> bool:
    return inicio() <= hora <= fin()


def texto_dias() -> str:
    d = sorted(dias())
    if d == list(range(d[0], d[-1] + 1)) and len(d) > 2:
        return f"de {_NOMBRES_DIAS[d[0]]} a {_NOMBRES_DIAS[d[-1]]}"
    return ", ".join(_NOMBRES_DIAS[x] for x in d)


def descripcion() -> str:
    """'de lunes a viernes, de 09:00 a 18:00'."""
    return f"{texto_dias()}, de {inicio():%H:%M} a {fin():%H:%M}"
