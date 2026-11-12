"""
Creación de cuentas de acceso y entrega de credenciales.

Lo usan Usuarios (cuenta creada a mano) y Personal (cuenta creada al registrar un empleado).
El código de acceso solo se conoce al generarse: se guarda cifrado y se muestra una vez.
"""
import re

from django.utils.http import url_has_allowed_host_and_scheme

from .codigos import generar_codigo_acceso, generar_usuario
from .codigos import _ascii
from .models import Rol, Usuario

CLAVE_SESION_CREDENCIALES = "credenciales_generadas"


def rol_para_cargo(nombre_cargo: str) -> str:
    """Rol de acceso según el cargo. Un cargo desconocido recibe el rol con menos permisos (vendedor)."""
    cargo = _ascii(nombre_cargo or "").lower()
    # Antes que «administr»: un «Administrador de TI» es del departamento de TI, no administrador del sistema.
    if "tecnolog" in cargo or re.search(r"\bti\b", cargo):
        return Rol.TI
    if "administr" in cargo:
        return Rol.ADMINISTRADOR
    if "cierre" in cargo:
        return Rol.ENCARGADO_CIERRE
    return Rol.VENDEDOR


def crear_cuenta_para_empleado(empleado, *, creado_por) -> tuple[Usuario, str]:
    """
    Crea el usuario (nombre.apellido) y su código de acceso, y lo vincula al empleado.
    Debe llamarse dentro de una transacción. Devuelve (usuario, código en texto plano).
    """
    correo = (empleado.email or "").strip().lower() or None
    if correo and Usuario.objects.filter(email__iexact=correo).exists():
        correo = None  # el correo es único entre usuarios; la cuenta se crea igual, sin correo

    usuario = Usuario(
        first_name=empleado.nombres, last_name=empleado.apellidos, email=correo,
        rol=rol_para_cargo(empleado.cargo.nombre), creado_por=creado_por, debe_cambiar_clave=False,
    )
    usuario.username = generar_usuario(empleado.nombres, empleado.apellidos)
    clave = generar_codigo_acceso(empleado.nombres, empleado.apellidos)
    usuario.set_password(clave)
    usuario.save()

    empleado.usuario = usuario
    empleado.save(update_fields=["usuario", "actualizado_en"])
    return usuario, clave


def preparar_credenciales(request, usuario, clave, *, nuevo: bool, volver: str | None = None) -> None:
    """Deja las credenciales en la sesión para que la pantalla de credenciales las muestre una vez."""
    if volver and not url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}):
        volver = None
    request.session[CLAVE_SESION_CREDENCIALES] = {
        "usuario": usuario.username, "clave": clave, "nombre": usuario.get_full_name(),
        "rol": usuario.get_rol_display(), "nuevo": nuevo, "volver": volver,
    }
