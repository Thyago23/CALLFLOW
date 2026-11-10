"""
Acceso al registro de chips: solo quien tenga el permiso `chips.gestionar_chips` asignado
directamente a su cuenta (ver apps/core/acceso.py). Se da o se quita con `python manage.py acceso_chips`.
"""
from apps.core.acceso import requiere_permiso_directo, tiene_permiso_directo

PERMISO = "gestionar_chips"


def tiene_acceso(usuario) -> bool:
    return tiene_permiso_directo(usuario, f"chips.{PERMISO}")


requiere_acceso = requiere_permiso_directo(f"chips.{PERMISO}", "El registro de chips es de uso exclusivo del coordinador.")
