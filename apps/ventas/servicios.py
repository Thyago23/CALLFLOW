"""
4.3 Módulo de ventas (SCRUM-38 a SCRUM-47).

Reglas que también cuida la base de datos (triggers de la migración 0002):
- Un DAMI nace en BORRADOR o ENVIADO; al enviarlo se crea solo su fila en la hoja de
  producción del mes; cada cambio de estado queda en el historial con el usuario.
- VENTA_CERRADA / NO_CONCRETADA solo las pone el módulo de cierre.
Aquí: quién puede ver/editar cada formulario, notificaciones y estadísticas.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol, Usuario

from .models import (AsignacionBase, BaseComercial, Cliente, FormularioDAMI, HojaProduccion, RegistroProduccion,
                     SolicitudBase)

Estado = FormularioDAMI.Estado
EN_CURSO = [Estado.ENVIADO, Estado.EN_GESTION]


# --------------------------------------------------------------------------- quién es quién
def empleado_de(usuario):
    return getattr(usuario, "empleado", None)


def es_vendedor(usuario) -> bool:
    return usuario.rol == Rol.VENDEDOR and empleado_de(usuario) is not None


def formularios_visibles(usuario):
    """El vendedor solo ve los suyos; encargados y administradores ven todos."""
    qs = FormularioDAMI.objects.select_related("cliente", "vendedor", "base_comercial", "encargado_asignado")
    if usuario.rol == Rol.VENDEDOR:
        empleado = empleado_de(usuario)
        return qs.filter(vendedor=empleado) if empleado else qs.none()
    return qs


def formulario_editable(usuario, formulario) -> bool:
    return (formulario.estado == Estado.BORRADOR and es_vendedor(usuario)
            and formulario.vendedor_id == empleado_de(usuario).pk and usuario.has_perm("ventas.change_formulariodami"))


def _notificar(usuarios, *, titulo, mensaje="", url="", tipo=Notificacion.Tipo.FORMULARIO_DAMI):
    Notificacion.objects.bulk_create([
        Notificacion(destinatario=u, tipo=tipo, titulo=titulo[:150], mensaje=mensaje, url=url) for u in usuarios
    ])


def usuarios_con_rol(*roles):
    return Usuario.objects.filter(rol__in=roles, is_active=True)


# --------------------------------------------------------------------------- clientes (SCRUM-39)
def buscar_cliente(tipo: str, identificacion: str) -> Cliente | None:
    return Cliente.objects.filter(tipo_identificacion=tipo, identificacion=identificacion).first()


def dami_en_curso(cliente: Cliente, excluir=None):
    qs = FormularioDAMI.objects.filter(cliente=cliente, estado__in=EN_CURSO).select_related("vendedor")
    return qs.exclude(pk=excluir.pk) if excluir else qs


def guardar_cliente(datos: dict) -> Cliente:
    """Reutiliza el cliente si ya existe (misma identificación) y actualiza sus datos de contacto."""
    campos = {k: datos[k] for k in ("nombres", "apellidos", "telefono", "telefono_alterno", "email", "direccion", "ciudad")}
    cliente, creado = Cliente.objects.get_or_create(
        tipo_identificacion=datos["tipo_identificacion"], identificacion=datos["identificacion"], defaults=campos,
    )
    if not creado:
        cambios = {k: v for k, v in campos.items() if v and getattr(cliente, k) != v}
        if cambios:
            for k, v in cambios.items():
                setattr(cliente, k, v)
            cliente.save(update_fields=[*cambios, "actualizado_en"])
    return cliente


# --------------------------------------------------------------------------- formulario DAMI (SCRUM-38, 40, 41)
@transaction.atomic
def guardar_formulario(usuario, datos: dict, formulario: FormularioDAMI | None = None, *, enviar: bool) -> FormularioDAMI:
    empleado = empleado_de(usuario)
    if not es_vendedor(usuario):
        raise PermissionDenied("Solo los vendedores registran formularios DAMI.")
    if formulario is not None and not formulario_editable(usuario, formulario):
        raise ValidationError("Este formulario ya no se puede modificar.")
    if enviar and not (datos.get("producto") or "").strip():
        raise ValidationError("Para enviar el formulario indique el producto.")

    cliente = guardar_cliente(datos)
    formulario = formulario or FormularioDAMI(vendedor=empleado)
    formulario.cliente = cliente
    formulario.producto = (datos.get("producto") or "").strip()
    formulario.tiene_tarjeta_activa = bool(datos.get("tiene_tarjeta_activa"))
    formulario.base_comercial = datos.get("base_comercial")
    formulario.observaciones = (datos.get("observaciones") or "").strip()
    formulario.save()
    if enviar:
        enviar_formulario(usuario, formulario)
    return formulario


@transaction.atomic
def enviar_formulario(usuario, formulario: FormularioDAMI) -> FormularioDAMI:
    """BORRADOR -> ENVIADO. La base de datos crea su fila en la hoja de producción (SCRUM-43)."""
    if not formulario_editable(usuario, formulario):
        raise ValidationError("Solo se pueden enviar los borradores propios.")
    if not formulario.producto:
        raise ValidationError("Para enviar el formulario indique el producto.")
    formulario.estado = Estado.ENVIADO
    formulario.enviado_en = timezone.now()
    formulario.save(update_fields=["estado", "enviado_en", "actualizado_en"])
    formulario.refresh_from_db()

    prioridad = " · PRIORITARIO (tarjeta activa)" if formulario.tiene_tarjeta_activa else ""
    _notificar(
        usuarios_con_rol(Rol.ENCARGADO_CIERRE),
        titulo=f"Nuevo DAMI para cierre: {formulario.cliente}{prioridad}",
        mensaje=f"{formulario} · {formulario.producto} · vendedor {formulario.vendedor}",
        url=reverse("ventas:formulario_detalle", args=[formulario.pk]),
    )
    return formulario


@transaction.atomic
def anular_formulario(usuario, formulario: FormularioDAMI) -> FormularioDAMI:
    if not formulario_editable(usuario, formulario):
        raise ValidationError("Solo se pueden anular los borradores propios.")
    formulario.estado = Estado.ANULADO
    formulario.save(update_fields=["estado", "actualizado_en"])
    return formulario


# --------------------------------------------------------------------------- hoja de producción (SCRUM-43, 44)
def hoja_del_mes(empleado, anio: int, mes: int) -> dict:
    hoja = HojaProduccion.objects.filter(vendedor=empleado, anio=anio, mes=mes).first()
    registros = (RegistroProduccion.objects.filter(hoja=hoja).select_related("formulario__cliente").order_by("fecha", "pk")
                 if hoja else RegistroProduccion.objects.none())
    return {"hoja": hoja, "registros": registros, "totales": totales(registros)}


def totales(registros) -> dict:
    estados = [r.estado for r in registros]
    cerradas = estados.count("VENTA_CERRADA")
    no_concretadas = estados.count("NO_CONCRETADA")
    gestionadas = cerradas + no_concretadas
    return {
        "enviados": len([e for e in estados if e != "ANULADO"]),
        "cerradas": cerradas,
        "no_concretadas": no_concretadas,
        "pendientes": estados.count("PENDIENTE"),
        "anulados": estados.count("ANULADO"),
        "total_vendido": sum((r.valor_venta or Decimal("0") for r in registros if r.estado == "VENTA_CERRADA"), Decimal("0")),
        "tasa_cierre": round(cerradas * 100 / gestionadas) if gestionadas else None,
    }


# --------------------------------------------------------------------------- panel del vendedor (SCRUM-45)
@dataclass
class Panel:
    mes: dict
    anterior: dict
    meta: Decimal | None
    borradores: int
    solicitud_pendiente: SolicitudBase | None

    @property
    def avance_meta(self):
        if not self.meta:
            return None
        return min(round(self.mes["total_vendido"] * 100 / self.meta), 999)

    @property
    def pendiente_meta(self):
        return max(self.meta - self.mes["total_vendido"], Decimal("0")) if self.meta else None

    @property
    def variacion(self):
        """% de cambio del total vendido frente al mes anterior."""
        antes = self.anterior["total_vendido"]
        if not antes:
            return None
        return round((self.mes["total_vendido"] - antes) * 100 / antes)


def _mes_anterior(anio: int, mes: int) -> tuple[int, int]:
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def panel_vendedor(empleado, hoy: date | None = None) -> Panel:
    from apps.cierre.models import MetaMensual

    hoy = hoy or timezone.localdate()
    anio_ant, mes_ant = _mes_anterior(hoy.year, hoy.month)
    meta = MetaMensual.objects.filter(vendedor=empleado, anio=hoy.year, mes=hoy.month).values_list("monto_meta", flat=True).first()
    return Panel(
        mes=hoja_del_mes(empleado, hoy.year, hoy.month)["totales"],
        anterior=hoja_del_mes(empleado, anio_ant, mes_ant)["totales"],
        meta=meta,
        borradores=FormularioDAMI.objects.filter(vendedor=empleado, estado=Estado.BORRADOR).count(),
        solicitud_pendiente=SolicitudBase.objects.filter(vendedor=empleado, estado=SolicitudBase.Estado.PENDIENTE).first(),
    )


# --------------------------------------------------------------------------- bases comerciales (SCRUM-46, 47)
def bases_del_vendedor(empleado):
    return BaseComercial.objects.filter(asignaciones__vendedor=empleado, activa=True).order_by("nombre")


@transaction.atomic
def solicitar_base(usuario, mensaje: str) -> SolicitudBase:
    empleado = empleado_de(usuario)
    if not es_vendedor(usuario):
        raise PermissionDenied("Solo los vendedores solicitan bases comerciales.")
    if SolicitudBase.objects.filter(vendedor=empleado, estado=SolicitudBase.Estado.PENDIENTE).exists():
        raise ValidationError("Ya tiene una solicitud pendiente. Espere la respuesta antes de pedir otra.")
    solicitud = SolicitudBase.objects.create(vendedor=empleado, mensaje=mensaje.strip())
    _notificar(
        usuarios_con_rol(Rol.ENCARGADO_CIERRE, Rol.ADMINISTRADOR),
        titulo=f"{empleado} solicita una nueva base comercial", mensaje=solicitud.mensaje[:300],
        url=reverse("ventas:bases"), tipo=Notificacion.Tipo.SOLICITUD_BASE,
    )
    return solicitud


def _usuario_del_vendedor(solicitud):
    usuario = solicitud.vendedor.usuario
    return [usuario] if usuario and usuario.is_active else []


@transaction.atomic
def atender_solicitud(solicitud: SolicitudBase, base: BaseComercial, usuario, respuesta: str = "") -> SolicitudBase:
    solicitud = SolicitudBase.objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.estado != SolicitudBase.Estado.PENDIENTE:
        raise ValidationError("Esta solicitud ya fue respondida.")
    if not base.activa:
        raise ValidationError("La base elegida está inactiva.")
    if AsignacionBase.objects.filter(base=base, vendedor=solicitud.vendedor).exists():
        raise ValidationError(f"{solicitud.vendedor} ya tiene asignada la base «{base.nombre}». Elija otra.")
    AsignacionBase.objects.create(base=base, vendedor=solicitud.vendedor, asignada_por=usuario)
    solicitud.estado = SolicitudBase.Estado.ATENDIDA
    solicitud.base_asignada = base
    solicitud.respuesta = respuesta.strip()
    solicitud.atendida_por = usuario
    solicitud.atendida_en = timezone.now()
    solicitud.save()
    _notificar(_usuario_del_vendedor(solicitud), titulo=f"Se le asignó la base «{base.nombre}»",
               mensaje=solicitud.respuesta, url=reverse("ventas:bases"), tipo=Notificacion.Tipo.SOLICITUD_BASE)
    return solicitud


@transaction.atomic
def rechazar_solicitud(solicitud: SolicitudBase, usuario, motivo: str) -> SolicitudBase:
    if not motivo.strip():
        raise ValidationError("Indique el motivo del rechazo.")
    solicitud = SolicitudBase.objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.estado != SolicitudBase.Estado.PENDIENTE:
        raise ValidationError("Esta solicitud ya fue respondida.")
    solicitud.estado = SolicitudBase.Estado.RECHAZADA
    solicitud.respuesta = motivo.strip()
    solicitud.atendida_por = usuario
    solicitud.atendida_en = timezone.now()
    solicitud.save()
    _notificar(_usuario_del_vendedor(solicitud), titulo="Su solicitud de base fue rechazada",
               mensaje=solicitud.respuesta, url=reverse("ventas:bases"), tipo=Notificacion.Tipo.SOLICITUD_BASE)
    return solicitud


@transaction.atomic
def asignar_base(base: BaseComercial, vendedores, usuario) -> int:
    nuevos = [v for v in vendedores if not AsignacionBase.objects.filter(base=base, vendedor=v).exists()]
    AsignacionBase.objects.bulk_create([AsignacionBase(base=base, vendedor=v, asignada_por=usuario) for v in nuevos])
    for v in nuevos:
        if v.usuario_id:
            _notificar([v.usuario], titulo=f"Se le asignó la base «{base.nombre}»", url=reverse("ventas:bases"),
                       tipo=Notificacion.Tipo.SOLICITUD_BASE)
    return len(nuevos)
