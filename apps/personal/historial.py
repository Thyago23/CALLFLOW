"""
Historial de cambios del empleado, a partir de la auditoría (core_registrocambio).

Los triggers de PostgreSQL guardan la fila completa antes y después de cada cambio, con el
usuario que lo hizo. Aquí se traduce a "qué campo cambió, de qué valor a qué valor".
"""
from datetime import date

from apps.core.models import RegistroCambio, Usuario

from .models import Cargo, Empleado

# Campos que se muestran, en este orden, con su nombre para el usuario.
CAMPOS = {
    "tipo_identificacion": "Tipo de identificación",
    "identificacion": "Identificación",
    "nombres": "Nombres",
    "apellidos": "Apellidos",
    "fecha_nacimiento": "Fecha de nacimiento",
    "telefono": "Teléfono",
    "email": "Correo electrónico",
    "direccion": "Dirección",
    "cargo_id": "Cargo",
    "tipo_contrato": "Tipo de contrato",
    "fecha_ingreso": "Fecha de ingreso",
    "estado": "Estado",
    "fecha_salida": "Fecha de salida",
    "banco": "Banco",
    "tipo_cuenta": "Tipo de cuenta",
    "numero_cuenta": "Número de cuenta",
    "usuario_id": "Cuenta de acceso",
}
OPCIONES = {
    "tipo_identificacion": dict(Empleado.TipoIdentificacion.choices),
    "tipo_contrato": dict(Empleado.TipoContrato.choices),
    "estado": dict(Empleado.Estado.choices),
    "tipo_cuenta": dict(Empleado.TipoCuenta.choices),
}


def _legible(campo, valor, cargos, usuarios):
    if valor in (None, ""):
        return "—"
    if campo == "cargo_id":
        return cargos.get(valor, f"#{valor}")
    if campo == "usuario_id":
        return usuarios.get(valor, f"#{valor}")
    if campo in OPCIONES:
        return OPCIONES[campo].get(valor, valor)
    if campo.startswith("fecha_"):
        try:
            return date.fromisoformat(str(valor)[:10]).strftime("%d/%m/%Y")
        except ValueError:
            return valor
    return valor


def cambios_de(empleado, limite: int = 50) -> list[dict]:
    registros = list(
        RegistroCambio.objects.filter(tabla="personal_empleado", registro_id=str(empleado.pk)).order_by("-fecha", "-id")[:limite]
    )
    ids_usuarios = {r.usuario_id for r in registros if r.usuario_id}
    ids_cargos = set()
    for r in registros:
        for fila in (r.datos_anteriores, r.datos_nuevos):
            if fila:
                ids_cargos.add(fila.get("cargo_id"))
                if fila.get("usuario_id"):
                    ids_usuarios.add(fila["usuario_id"])
    cargos = dict(Cargo.objects.filter(pk__in=ids_cargos - {None}).values_list("pk", "nombre"))
    usuarios = {u.pk: str(u) for u in Usuario.objects.filter(pk__in=ids_usuarios)}

    historial = []
    for r in registros:
        antes, despues = r.datos_anteriores or {}, r.datos_nuevos or {}
        if r.accion == RegistroCambio.Accion.UPDATE:
            cambios = [
                {"campo": etiqueta, "antes": _legible(c, antes.get(c), cargos, usuarios), "despues": _legible(c, despues.get(c), cargos, usuarios)}
                for c, etiqueta in CAMPOS.items() if antes.get(c) != despues.get(c)
            ]
            if not cambios:
                continue  # solo cambió la fecha de actualización
        else:
            cambios = []
        historial.append({
            "fecha": r.fecha, "accion": r.get_accion_display(), "es_alta": r.accion == RegistroCambio.Accion.INSERT,
            "usuario": usuarios.get(r.usuario_id, "Sistema"), "cambios": cambios,
        })
    return historial
