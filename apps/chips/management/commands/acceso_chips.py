"""
Da o quita el acceso al registro de chips a una cuenta (permiso directo, no por rol ni grupo).

    python manage.py acceso_chips marlon.rosero          # da el acceso
    python manage.py acceso_chips marlon.rosero --quitar # lo quita
    python manage.py acceso_chips --ver                  # quién lo tiene
"""
from apps.core.comando_acceso import ComandoAccesoExclusivo


class Command(ComandoAccesoExclusivo):
    help = "Da o quita el acceso exclusivo al registro de chips."
    app_label, codename, nombre = "chips", "gestionar_chips", "al registro de chips"
