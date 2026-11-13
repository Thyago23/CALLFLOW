"""
Salida de empleados (despedido, renunció, fin de contrato).

El empleado NUNCA se borra: queda como «ex empleado» (estado Desvinculado) con su fecha y motivo de salida,
y conserva todo su historial (nómina, asistencia, ventas, documentos y auditoría). Deja de aparecer en el
listado de Personal, en los selectores del sistema y en la asistencia del día, y su cuenta de acceso se desactiva.
"""
from dataclasses import dataclass, field
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Empleado


@dataclass
class Resultado:
    empleado: Empleado
    avisos: list[str] = field(default_factory=list)


def registrar_salida(empleado: Empleado, *, fecha: date, motivo: str, motivo_otro: str = "", detalle: str = "") -> Resultado:
    """Marca al empleado como ex empleado. También sirve para corregir la fecha o el motivo de una salida ya registrada."""
    if motivo not in Empleado.MotivoSalida.values:
        raise ValidationError("Elija el motivo de la salida.")
    motivo_otro = " ".join(motivo_otro.split()) if motivo == Empleado.MotivoSalida.OTRO else ""
    if motivo == Empleado.MotivoSalida.OTRO and not motivo_otro:
        raise ValidationError("Escriba cuál fue el motivo de la salida.")
    if fecha < empleado.fecha_ingreso:
        raise ValidationError("La fecha de salida no puede ser anterior a la fecha de ingreso.")
    with transaction.atomic():
        empleado = Empleado.objects.select_for_update(of=("self",)).select_related("usuario").get(pk=empleado.pk)
        empleado.estado = Empleado.Estado.DESVINCULADO
        empleado.fecha_salida, empleado.motivo_salida, empleado.detalle_salida = fecha, motivo, detalle.strip()
        empleado.motivo_otro = motivo_otro
        empleado.save(update_fields=["estado", "fecha_salida", "motivo_salida", "motivo_otro", "detalle_salida",
                                     "actualizado_en"])
        avisos = _desactivar_cuenta(empleado)
    return Resultado(empleado, avisos)


def reintegrar(empleado: Empleado) -> Resultado:
    """Vuelve a contratar a un ex empleado: queda Activo y se reactiva su cuenta de acceso."""
    with transaction.atomic():
        empleado = Empleado.objects.select_for_update(of=("self",)).select_related("usuario").get(pk=empleado.pk)
        if not empleado.es_ex_empleado:
            raise ValidationError("El empleado no está registrado como ex empleado.")
        empleado.estado = Empleado.Estado.ACTIVO
        empleado.fecha_salida, empleado.motivo_salida, empleado.motivo_otro, empleado.detalle_salida = None, "", "", ""
        empleado.save(update_fields=["estado", "fecha_salida", "motivo_salida", "motivo_otro", "detalle_salida",
                                     "actualizado_en"])
        avisos = []
        usuario = empleado.usuario
        if usuario and not usuario.is_active:
            usuario.is_active = True
            usuario.save()  # save() completo: dispara la sincronización del grupo de permisos por rol
            avisos.append(f"Se reactivó la cuenta {usuario.username}: puede volver a ingresar con su código de acceso.")
    return Resultado(empleado, avisos)


def _desactivar_cuenta(empleado: Empleado) -> list[str]:
    usuario = empleado.usuario
    if not usuario or not usuario.is_active:
        return []
    if usuario.protegida:
        # La base de datos no permite desactivar las cuentas fijas de TI.
        return [f"La cuenta {usuario.username} es una cuenta fija de TI: sigue activa aunque el empleado haya salido. "
                "Cambie su código de acceso si es necesario."]
    usuario.is_active = False  # un ex empleado ya no puede ingresar
    usuario.save()
    return [f"Se desactivó la cuenta {usuario.username}: ya no puede ingresar a CALLFLOW."]
