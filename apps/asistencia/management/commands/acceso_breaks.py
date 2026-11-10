"""
Da o quita a una cuenta el permiso de dar breaks (permiso directo, no por rol ni grupo).

    python manage.py acceso_breaks marlon.rosero          # da el acceso
    python manage.py acceso_breaks marlon.rosero --quitar # lo quita
    python manage.py acceso_breaks --ver                  # quién lo tiene
"""
from apps.core.comando_acceso import ComandoAccesoExclusivo


class Command(ComandoAccesoExclusivo):
    help = "Da o quita el permiso exclusivo de dar breaks a los vendedores."
    app_label, codename, nombre = "asistencia", "gestionar_breaks", "a los breaks"
