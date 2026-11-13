"""
4.4 Módulo de cierre de ventas (SCRUM-48 a SCRUM-59).

Flujo de cierre en etapas (definido con la empresa):
  1. El vendedor envía el DAMI: llega a quien hace el **filtro y cierre**.
  2. Filtro: si el cliente no pasa, se registra «no concretada». Si pasa, se envía a **gestión**.
  3. Gestión: si no es exitosa, quien gestiona registra «no concretada». Si es exitosa, lo devuelve.
  4. Cierre final: quien filtró registra «venta cerrada» (con su valor) o «no concretada».
Quién hace cada etapa lo configura el administrador (ConfiguracionCierre). El administrador también
puede reasignar la etapa actual a otro supervisor y registrar en nombre de quien la tiene.

Modificaciones simultáneas (SCRUM-59): cada acción sobre un DAMI lleva la `version` que el
usuario tenía en pantalla. La base de datos incrementa la versión en cada cambio, así que si
otro encargado guardó antes, la actualización no encuentra la fila y se avisa en lugar de
sobrescribir. La base también impide dos resultados para el mismo formulario.
"""
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from cryptography.fernet import InvalidToken
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol
from apps.personal.models import Empleado
from apps.ventas.models import FormularioDAMI
from apps.ventas.servicios import _notificar, empleado_de, es_administrador
from apps.ventas.tarjetas import descifrar

from .models import (ConfiguracionCierre, GestionCierre, MetaMensual, MovimientoAsignacion, PreferenciaPanel,
                     ResumenVendedorMes)

logger = logging.getLogger(__name__)
Estado = FormularioDAMI.Estado
Etapa = FormularioDAMI.Etapa
EN_COLA = [Estado.ENVIADO, Estado.EN_GESTION]

MOTIVOS_NO_CONCRETADA = [
    "El cliente no pasó el filtro",
    "La gestión no fue exitosa",
    "El cliente no contesta",
    "El cliente no está interesado",
    "La tarjeta fue rechazada",
    "Datos del cliente incorrectos",
    "El cliente ya tiene el producto",
    "Otro motivo",
]


class Conflicto(ValidationError):
    """Otro usuario modificó el formulario mientras este lo tenía abierto."""

    MENSAJE = ("Otra persona modificó este formulario mientras usted lo tenía abierto. "
               "No se guardó nada: recargue para ver los datos actuales.")

    def __init__(self, mensaje: str | None = None):
        super().__init__(mensaje or self.MENSAJE)


# --------------------------------------------------------------------------- quién es quién
def es_encargado(usuario) -> bool:
    return usuario.rol == Rol.ENCARGADO_CIERRE and empleado_de(usuario) is not None


def sin_ficha(usuario) -> bool:
    """Encargado de cierre cuya cuenta no está vinculada a un empleado: no puede gestionar ni registrar cierres."""
    return usuario.rol == Rol.ENCARGADO_CIERRE and empleado_de(usuario) is None


def encargados_activos():
    return (Empleado.objects.filter(usuario__rol=Rol.ENCARGADO_CIERRE, usuario__is_active=True)
            .exclude(estado=Empleado.Estado.DESVINCULADO)
            .select_related("usuario").order_by("apellidos", "nombres"))


def vendedores_activos():
    # Con su usuario: el tablero y el panel muestran el apodo y la foto de cada uno (sin una consulta por fila).
    return (Empleado.objects.filter(usuario__rol=Rol.VENDEDOR, estado=Empleado.Estado.ACTIVO)
            .select_related("usuario").order_by("apellidos", "nombres"))


def url_formulario(formulario) -> str:
    return reverse("cierre:formulario", args=[formulario.pk])


# --------------------------------------------------------------------------- responsables de cada etapa
def configuracion() -> ConfiguracionCierre:
    return (ConfiguracionCierre.objects.select_related("responsable_filtro__usuario", "responsable_gestion__usuario")
            .get_or_create(pk=1)[0])


def _disponible(empleado):
    """Solo cuenta si sigue siendo un supervisor activo (no desvinculado ni con la cuenta desactivada)."""
    return empleado if empleado and encargados_activos().filter(pk=empleado.pk).exists() else None


def responsable_filtro():
    return _disponible(configuracion().responsable_filtro)


def responsable_gestion():
    return _disponible(configuracion().responsable_gestion)


@transaction.atomic
def configurar_responsables(usuario, *, filtro: Empleado | None, gestion: Empleado | None) -> ConfiguracionCierre:
    if not es_administrador(usuario):
        raise PermissionDenied("Solo el administrador configura quién hace cada etapa del cierre.")
    for empleado in (filtro, gestion):
        if empleado is not None and _disponible(empleado) is None:
            raise ValidationError(f"{empleado} no es un supervisor activo.")
    config = ConfiguracionCierre.objects.select_for_update().get_or_create(pk=1)[0]
    config.responsable_filtro, config.responsable_gestion, config.actualizado_por = filtro, gestion, usuario
    config.save()
    return config


def a_cargo(formulario):
    """Quién debe actuar ahora sobre el DAMI (None: cualquier supervisor, si no hay responsable de filtro)."""
    if formulario.estado == Estado.ENVIADO:
        return responsable_filtro()
    if formulario.estado == Estado.EN_GESTION:
        return formulario.encargado_asignado
    return None


def puede_actuar(usuario, formulario) -> bool:
    """El supervisor de la etapa actual (o el administrador en su nombre)."""
    if formulario.estado not in EN_COLA:
        return False
    if es_administrador(usuario):
        return True
    if not es_encargado(usuario):
        return False
    yo = empleado_de(usuario)
    if yo.pk == formulario.vendedor_id:
        return False
    if formulario.estado == Estado.ENVIADO:
        filtro = responsable_filtro()
        return filtro is None or filtro.pk == yo.pk
    return formulario.encargado_asignado_id == yo.pk


def resultados_permitidos(formulario) -> list[str]:
    """La venta solo se cierra en el cierre final; en el filtro y en la gestión solo se puede descartar."""
    if formulario.estado == Estado.EN_GESTION and formulario.etapa == Etapa.CIERRE:
        return [GestionCierre.Resultado.VENTA_CERRADA, GestionCierre.Resultado.NO_CONCRETADA]
    return [GestionCierre.Resultado.NO_CONCRETADA]


def numero_tarjeta_visible(usuario, formulario, tarjeta) -> str | None:
    """Número completo, en bloques de 4: solo para el supervisor que tiene el DAMI en su etapa (filtro, gestión o cierre)."""
    if tarjeta is None or not es_encargado(usuario) or not puede_actuar(usuario, formulario):
        return None
    try:
        numero = descifrar(tarjeta.numero_cifrado)
    except InvalidToken:
        logger.error("No se pudo descifrar la tarjeta del formulario %s: revise CLAVE_TARJETAS.", formulario.pk)
        return None
    return " ".join(numero[i:i + 4] for i in range(0, len(numero), 4))


# --------------------------------------------------------------------------- bandeja (SCRUM-48, 49)
def bandeja():
    """Formularios por gestionar: primero los de tarjeta activa, luego del más antiguo al más reciente."""
    return (FormularioDAMI.objects.filter(estado__in=EN_COLA)
            .select_related("cliente", "vendedor", "encargado_asignado", "tarjeta")
            .order_by("prioridad", "enviado_en", "pk"))


def gestionados():
    return (FormularioDAMI.objects.filter(estado__in=[Estado.VENTA_CERRADA, Estado.NO_CONCRETADA])
            .select_related("cliente", "vendedor", "encargado_asignado", "gestion", "tarjeta")  # la tabla muestra la tarjeta
            .order_by("-gestion__gestionado_en", "-pk"))


def mis_pendientes(qs, usuario):
    """Los DAMI de la bandeja que le toca atender a este supervisor."""
    yo = empleado_de(usuario)
    if yo is None:
        return qs.none()
    filtro = responsable_filtro()
    mios = Q(estado=Estado.EN_GESTION, encargado_asignado=yo)
    if filtro is None or filtro.pk == yo.pk:
        mios |= Q(estado=Estado.ENVIADO)
    return qs.filter(mios).exclude(vendedor=yo)


def pendientes_de(usuario) -> int:
    """Número del menú: lo que espera a este supervisor (al administrador: lo recién enviado, sin abrir)."""
    if es_encargado(usuario):
        return mis_pendientes(FormularioDAMI.objects.filter(estado__in=EN_COLA), usuario).count()
    return FormularioDAMI.objects.filter(estado=Estado.ENVIADO).count()


def resumen_bandeja() -> dict:
    cola = FormularioDAMI.objects.filter(estado__in=EN_COLA)
    return {
        "en_cola": cola.count(),
        "por_filtrar": cola.filter(etapa=Etapa.FILTRO).count(),
        "en_gestion": cola.filter(etapa=Etapa.GESTION).count(),
        "por_cerrar": cola.filter(etapa=Etapa.CIERRE).count(),
        "prioritarios": cola.filter(prioridad=1).count(),
    }


# --------------------------------------------------------------------------- pasar de una etapa a otra (SCRUM-51, 59)
def _actualizar_con_version(formulario, version: int, filtro: Q, **cambios) -> FormularioDAMI:
    """UPDATE ... WHERE id = ? AND version = ?: si alguien guardó antes, no se toca nada."""
    filas = (FormularioDAMI.objects.filter(filtro, pk=formulario.pk, version=version)
             .update(**cambios, actualizado_en=timezone.now()))
    if filas != 1:
        raise Conflicto()
    return FormularioDAMI.objects.select_related("cliente", "vendedor", "encargado_asignado").get(pk=formulario.pk)


def _marcar_avisos_atendidos(url: str) -> None:
    """La notificación deja de estar pendiente para todos, así nadie la atiende dos veces."""
    Notificacion.objects.filter(url=url, leida=False).update(leida=True, leida_en=timezone.now())


def _quien_actua(usuario, formulario):
    """El empleado en cuyo nombre queda la acción: el de la etapa, o quien actúa si es supervisor."""
    if formulario.encargado_asignado_id:
        return formulario.encargado_asignado
    if es_encargado(usuario):
        return empleado_de(usuario)
    return responsable_filtro()


def _mover(usuario, formulario, version: int, *, etapa_actual: str, etapa: str, destino: Empleado, accion: str,
           nota: str, titulo: str) -> FormularioDAMI:
    if destino.pk == formulario.vendedor_id:
        raise ValidationError(f"{destino} es el vendedor de este DAMI: no puede gestionarlo.")
    anterior = _quien_actua(usuario, formulario)
    try:
        with transaction.atomic():
            actualizado = _actualizar_con_version(
                formulario, version, Q(estado__in=EN_COLA, etapa=etapa_actual),
                estado=Estado.EN_GESTION, etapa=etapa, encargado_asignado=destino, asignado_en=timezone.now(),
            )
    except IntegrityError as error:
        raise ValidationError("No se pudo pasar el formulario a la siguiente etapa. Recargue e intente de nuevo.") from error
    MovimientoAsignacion.objects.create(formulario=actualizado, accion=accion, de_encargado=anterior,
                                        a_encargado=destino, usuario=usuario, nota=nota.strip())
    _marcar_avisos_atendidos(url_formulario(actualizado))
    if destino.usuario_id:
        de = usuario.get_full_name() or usuario.username
        _notificar([destino.usuario], titulo=titulo.format(f=actualizado),
                   mensaje=f"De {de}" + (f": {nota.strip()}" if nota.strip() else "") + f" · cliente {actualizado.cliente.identificacion}",
                   url=url_formulario(actualizado))
    return actualizado


@transaction.atomic
def pasar_filtro(usuario, formulario, version: int, nota: str = "") -> FormularioDAMI:
    """El cliente pasó el filtro: el DAMI va a quien hace la gestión."""
    if not puede_actuar(usuario, formulario):
        raise PermissionDenied("Este formulario no está en su etapa.")
    if formulario.etapa != Etapa.FILTRO:
        raise ValidationError("Este formulario ya pasó el filtro.")
    gestion = responsable_gestion()
    if gestion is None:
        raise ValidationError("Todavía no está configurado quién hace la gestión. Pida al administrador que lo elija "
                              "en la bandeja de cierre (Responsables).")
    return _mover(usuario, formulario, version, etapa_actual=Etapa.FILTRO, etapa=Etapa.GESTION, destino=gestion,
                  accion=MovimientoAsignacion.Accion.PASA_FILTRO, nota=nota,
                  titulo="{f} pasó el filtro: le toca gestionarlo")


def _quien_filtro(formulario):
    movimiento = (formulario.movimientos_asignacion.filter(accion=MovimientoAsignacion.Accion.PASA_FILTRO)
                  .order_by("-fecha").first())
    return _disponible(movimiento.de_encargado) if movimiento else None


@transaction.atomic
def devolver_a_cierre(usuario, formulario, version: int, nota: str = "") -> FormularioDAMI:
    """La gestión fue exitosa: el DAMI vuelve a quien filtró para que cierre la venta."""
    if not puede_actuar(usuario, formulario):
        raise PermissionDenied("Este formulario no está en su etapa.")
    if formulario.estado != Estado.EN_GESTION or formulario.etapa != Etapa.GESTION:
        raise ValidationError("Solo se devuelve para el cierre un formulario que está en gestión.")
    destino = responsable_filtro() or _quien_filtro(formulario)
    if destino is None:
        raise ValidationError("No hay a quién devolverlo: pida al administrador que configure quién hace el filtro y cierre.")
    return _mover(usuario, formulario, version, etapa_actual=Etapa.GESTION, etapa=Etapa.CIERRE, destino=destino,
                  accion=MovimientoAsignacion.Accion.A_CIERRE, nota=nota,
                  titulo="{f}: gestión exitosa, listo para cerrar la venta")


@transaction.atomic
def reasignar(usuario, formulario, version: int, encargado: Empleado) -> FormularioDAMI:
    """El administrador pasa la etapa actual a otro supervisor (p. ej. si el responsable faltó)."""
    if not es_administrador(usuario):
        raise PermissionDenied("Solo el administrador reasigna formularios.")
    if formulario.estado not in EN_COLA:
        raise ValidationError("Solo se reasignan formularios que siguen en la cola.")
    if not encargados_activos().filter(pk=encargado.pk).exists():
        raise ValidationError("Elija un supervisor activo.")
    if encargado.pk == formulario.vendedor_id:
        raise ValidationError("No se puede asignar el formulario al mismo vendedor que lo registró.")
    anterior = formulario.encargado_asignado
    if anterior and anterior.pk == encargado.pk:
        raise ValidationError(f"El formulario ya está asignado a {encargado}.")
    actualizado = _actualizar_con_version(
        formulario, version, Q(estado__in=EN_COLA),
        estado=Estado.EN_GESTION, encargado_asignado=encargado, asignado_en=timezone.now(),
    )
    MovimientoAsignacion.objects.create(formulario=actualizado, accion=MovimientoAsignacion.Accion.REASIGNAR,
                                        de_encargado=anterior, a_encargado=encargado, usuario=usuario)
    if encargado.usuario_id:
        _notificar([encargado.usuario],
                   titulo=f"Se le asignó {actualizado} ({actualizado.get_etapa_display().lower()})",
                   mensaje=f"Cliente {actualizado.cliente.identificacion} · asesor {actualizado.vendedor}",
                   url=url_formulario(actualizado))
    return actualizado


# --------------------------------------------------------------------------- resultado (SCRUM-52, 53, 59)
def _validar_resultado(resultado: str, valor, motivo: str):
    if resultado == GestionCierre.Resultado.VENTA_CERRADA:
        if valor is None or valor <= 0:
            raise ValidationError({"valor_venta": "Indique el valor de la venta (mayor a cero)."})
        return valor, ""
    if resultado == GestionCierre.Resultado.NO_CONCRETADA:
        if not motivo:
            raise ValidationError({"motivo": "Indique por qué no se concretó la venta."})
        return None, motivo
    raise ValidationError({"resultado": "Elija un resultado válido."})


@transaction.atomic
def registrar_resultado(usuario, formulario, version: int, *, resultado: str, valor_venta: Decimal | None = None,
                        motivo: str = "", observaciones: str = "") -> GestionCierre:
    """
    Registra el resultado: «no concretada» en cualquier etapa (no pasó el filtro / gestión no exitosa) y
    «venta cerrada» solo en el cierre final. La base de datos actualiza en la misma transacción el DAMI y
    la hoja de producción del vendedor (SCRUM-53): si algo falla, no se guarda nada.
    """
    valor, motivo = _validar_resultado(resultado, valor_venta, motivo.strip())
    bloqueado = (FormularioDAMI.objects.select_for_update(of=("self",)).select_related("encargado_asignado", "vendedor")
                 .get(pk=formulario.pk))
    if bloqueado.version != version:
        raise Conflicto()
    if bloqueado.estado not in EN_COLA:
        raise ValidationError("Este formulario ya no está pendiente de cierre.")
    if not puede_actuar(usuario, bloqueado):
        quien = a_cargo(bloqueado)
        raise PermissionDenied(f"Este formulario lo tiene {quien} ({bloqueado.get_etapa_display().lower()})." if quien
                               else "Este formulario no está en su etapa.")
    if resultado not in resultados_permitidos(bloqueado):
        raise ValidationError({"resultado": "La venta se cierra en el cierre final, después de la gestión."})
    encargado = _quien_actua(usuario, bloqueado)
    if encargado is None:
        raise ValidationError("Configure quién hace el filtro y cierre para registrar el resultado.")
    etapa = bloqueado.etapa
    try:
        with transaction.atomic():
            gestion = GestionCierre.objects.create(
                formulario=bloqueado, encargado=encargado, resultado=resultado,
                valor_venta=valor, motivo_no_concretada=motivo, observaciones=observaciones.strip(),
            )
    except IntegrityError as error:  # la base de datos no admite dos resultados para un mismo formulario
        raise Conflicto("Este formulario ya tiene un resultado registrado.") from error

    vendedor = bloqueado.vendedor
    if vendedor.usuario_id:
        texto = (f"Venta cerrada por ${valor:,.2f}" if valor else f"Venta no concretada: {motivo}")
        _notificar([vendedor.usuario], titulo=f"Resultado de {bloqueado}: {gestion.get_resultado_display().lower()}",
                   mensaje=texto, url=reverse("ventas:hoja"))
    if etapa == Etapa.GESTION:  # quien lo filtró se entera de que la gestión no resultó
        filtro = responsable_filtro() or _quien_filtro(bloqueado)
        if filtro and filtro.usuario_id and filtro.pk != encargado.pk:
            _notificar([filtro.usuario], titulo=f"{bloqueado}: la gestión no fue exitosa",
                       mensaje=f"{encargado} lo registró como no concretada: {motivo}", url=url_formulario(bloqueado))
    return gestion


@transaction.atomic
def corregir_resultado(usuario, gestion: GestionCierre, *, resultado: str, valor_venta: Decimal | None,
                       motivo: str, motivo_correccion: str) -> GestionCierre:
    """Solo el administrador corrige un resultado; el cambio queda en el registro de cambios (auditoría)."""
    if not es_administrador(usuario):
        raise PermissionDenied("Solo el administrador corrige resultados ya registrados.")
    if not motivo_correccion.strip():
        raise ValidationError({"motivo_correccion": "Indique por qué corrige el resultado."})
    valor, motivo = _validar_resultado(resultado, valor_venta, motivo.strip())
    gestion = GestionCierre.objects.select_for_update().get(pk=gestion.pk)
    anterior = f"{gestion.get_resultado_display()}" + (f" ${gestion.valor_venta:,.2f}" if gestion.valor_venta else "")
    gestion.resultado, gestion.valor_venta, gestion.motivo_no_concretada = resultado, valor, motivo
    nota = (f"[{timezone.localtime():%d/%m/%Y %H:%M}] Corrección de {usuario.get_full_name() or usuario.username} "
            f"(antes: {anterior}): {motivo_correccion.strip()}")
    gestion.observaciones = f"{gestion.observaciones}\n{nota}".strip()
    gestion.save()
    return gestion


# --------------------------------------------------------------------------- historial del formulario
def historial(formulario) -> list[dict]:
    """Cambios de estado (trigger de ventas) y movimientos entre etapas, en orden."""
    from apps.core.models import Usuario

    estados = list(formulario.historial.all())
    movimientos = list(formulario.movimientos_asignacion.select_related("de_encargado", "a_encargado", "usuario"))
    nombres = {u.pk: u.get_full_name() or u.username for u in Usuario.objects.filter(pk__in={h.usuario_id for h in estados})}
    etiquetas = dict(Estado.choices)
    filas = [{"fecha": h.fecha, "tipo": "estado", "estado": h.estado_nuevo,
              "texto": etiquetas.get(h.estado_nuevo, h.estado_nuevo), "usuario": nombres.get(h.usuario_id, "Sistema")}
             for h in estados]
    A = MovimientoAsignacion.Accion
    for m in movimientos:
        if m.accion == A.PASA_FILTRO:
            texto = f"Pasó el filtro: enviado a gestión con {m.a_encargado}"
        elif m.accion == A.A_CIERRE:
            texto = f"Gestión exitosa: devuelto a {m.a_encargado} para el cierre"
        elif m.accion == A.REASIGNAR:
            texto = f"Reasignado de {m.de_encargado or 'la cola'} a {m.a_encargado}"
        elif m.accion == A.TOMAR:
            texto = f"Lo tomó {m.a_encargado}"
        else:
            texto = f"{m.de_encargado} lo devolvió a la cola"
        filas.append({"fecha": m.fecha, "tipo": "asignacion", "texto": texto, "nota": m.nota,
                      "usuario": m.usuario.get_full_name() if m.usuario else "Sistema"})
    return sorted(filas, key=lambda f: f["fecha"])


# --------------------------------------------------------------------------- metas (SCRUM-55)
def _mes_anterior(anio: int, mes: int) -> tuple[int, int]:
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def metas_del_mes(anio: int, mes: int) -> list[dict]:
    metas = {m.vendedor_id: m for m in MetaMensual.objects.filter(anio=anio, mes=mes)}
    vendedores = list(vendedores_activos())
    ids = {v.pk for v in vendedores}
    vendedores += list(Empleado.objects.filter(pk__in=set(metas) - ids))  # vendedores ya inactivos con meta
    return [{"vendedor": v, "meta": metas.get(v.pk)} for v in vendedores]


@transaction.atomic
def guardar_metas(usuario, anio: int, mes: int, montos: dict[int, Decimal]) -> int:
    """
    `montos`: {vendedor_id: monto}. Crea o cambia la meta; cada cambio lo audita la base de datos.
    Solo se aceptan vendedores activos: el id llega del formulario («meta-<id>») y podría ser de otro
    empleado (un encargado aparecería en el panel y en la TV) o no existir (error al confirmar la transacción).
    """
    validos = set(vendedores_activos().filter(pk__in=montos).values_list("pk", flat=True))
    cambios = 0
    for vendedor_id, monto in montos.items():
        if vendedor_id not in validos:
            continue
        meta = MetaMensual.objects.filter(vendedor_id=vendedor_id, anio=anio, mes=mes).first()
        if meta and meta.monto_meta == monto:
            continue
        if meta:
            meta.monto_meta, meta.definida_por = monto, usuario
            meta.save(update_fields=["monto_meta", "definida_por", "actualizado_en"])
        else:
            MetaMensual.objects.create(vendedor_id=vendedor_id, anio=anio, mes=mes, monto_meta=monto, definida_por=usuario)
        cambios += 1
    return cambios


@transaction.atomic
def copiar_metas_mes_anterior(usuario, anio: int, mes: int) -> int:
    """Copia las metas del mes anterior a los vendedores que aún no tienen meta este mes."""
    anio_ant, mes_ant = _mes_anterior(anio, mes)
    ya_tienen = set(MetaMensual.objects.filter(anio=anio, mes=mes).values_list("vendedor_id", flat=True))
    activos = set(vendedores_activos().values_list("pk", flat=True))
    nuevas = [MetaMensual(vendedor_id=m.vendedor_id, anio=anio, mes=mes, monto_meta=m.monto_meta, definida_por=usuario)
              for m in MetaMensual.objects.filter(anio=anio_ant, mes=mes_ant)
              if m.vendedor_id not in ya_tienen and m.vendedor_id in activos]
    for meta in nuevas:  # save() uno por uno para que el trigger de auditoría registre cada meta
        meta.save()
    return len(nuevas)


# --------------------------------------------------------------------------- panel comercial (SCRUM-54, 56, 57, 58)
CRITERIO_RENDIMIENTO = "Mayor valor vendido en el mes; si hay empate, más ventas cerradas."


@dataclass
class FilaPanel:
    vendedor: Empleado
    total_formularios: int = 0
    ventas_cerradas: int = 0
    ventas_no_concretadas: int = 0
    pendientes: int = 0
    total_vendido: Decimal = Decimal("0")
    monto_meta: Decimal | None = None
    pendiente_meta: Decimal | None = None
    porcentaje_meta: Decimal | None = None
    posicion: int | None = None

    @property
    def cumplio_meta(self) -> bool:
        return self.monto_meta is not None and self.total_vendido >= self.monto_meta

    @property
    def barra(self) -> int:
        return min(int(self.porcentaje_meta or 0), 100)


@dataclass
class Panel:
    anio: int
    mes: int
    filas: list[FilaPanel]
    ocultar_valores: bool
    totales: dict = field(default_factory=dict)

    @property
    def mejor(self) -> FilaPanel | None:
        primero = self.filas[0] if self.filas else None
        return primero if primero and primero.total_vendido > 0 else None


def panel_comercial(anio: int, mes: int, usuario) -> Panel:
    """Los totales salen de la vista de PostgreSQL, la misma fuente que la hoja de producción."""
    resumen = {r.vendedor_id: r for r in ResumenVendedorMes.objects.filter(anio=anio, mes=mes)}
    vendedores = {v.pk: v for v in vendedores_activos()}
    vendedores |= {v.pk: v for v in Empleado.objects.filter(pk__in=set(resumen) - set(vendedores)).select_related("usuario")}
    filas = []
    for pk, vendedor in vendedores.items():
        r = resumen.get(pk)
        fila = FilaPanel(vendedor=vendedor)
        if r:
            for campo in ("total_formularios", "ventas_cerradas", "ventas_no_concretadas", "pendientes", "total_vendido",
                          "monto_meta", "pendiente_meta", "porcentaje_meta"):
                setattr(fila, campo, getattr(r, campo))
        filas.append(fila)
    filas.sort(key=lambda f: (-f.total_vendido, -f.ventas_cerradas, str(f.vendedor)))
    posicion, anterior = 0, None
    for i, fila in enumerate(filas, start=1):
        clave = (fila.total_vendido, fila.ventas_cerradas)
        if clave != anterior:
            posicion, anterior = i, clave
        fila.posicion = posicion

    con_meta = [f for f in filas if f.monto_meta is not None]
    meta_total = sum((f.monto_meta for f in con_meta), Decimal("0"))
    vendido_con_meta = sum((f.total_vendido for f in con_meta), Decimal("0"))
    totales = {
        "total_formularios": sum(f.total_formularios for f in filas),
        "ventas_cerradas": sum(f.ventas_cerradas for f in filas),
        "ventas_no_concretadas": sum(f.ventas_no_concretadas for f in filas),
        "pendientes": sum(f.pendientes for f in filas),
        "total_vendido": sum((f.total_vendido for f in filas), Decimal("0")),
        "monto_meta": meta_total if con_meta else None,
        "pendiente_meta": max(meta_total - vendido_con_meta, Decimal("0")) if con_meta else None,
        "porcentaje_meta": round(vendido_con_meta * 100 / meta_total) if meta_total else None,
        "cumplieron": sum(f.cumplio_meta for f in filas),
    }
    return Panel(anio=anio, mes=mes, filas=filas, ocultar_valores=ocultar_valores(usuario), totales=totales)


def ocultar_valores(usuario) -> bool:
    return PreferenciaPanel.objects.filter(usuario=usuario, ocultar_valores=True).exists()


def cambiar_ocultar_valores(usuario, ocultar: bool) -> None:
    PreferenciaPanel.objects.update_or_create(usuario=usuario, defaults={"ocultar_valores": ocultar})


# --------------------------------------------------------------------------- tablero de ventas (para la TV del call)
# Tonos fríos que combinan con el azul de CALLFLOW (fondo de las iniciales cuando no hay foto).
COLORES_TABLERO = ["#7db8ea", "#9cc9f0", "#5fa8e0", "#8fd3e8", "#a9b8f0", "#6cc4c0", "#b7d4f2", "#82a9e6", "#9fdcd2", "#c3cff5"]


@dataclass
class FilaTablero:
    fila: FilaPanel
    nombre: str
    iniciales: str
    color: str
    ancho: int = 0  # largo de la barra: lo vendido respecto del primero (100)
    puesto: int = 0  # 1, 2 o 3 si está en el podio
    url_foto: str = ""

    def __getattr__(self, nombre):
        return getattr(self.fila, nombre)


def _nombre_corto(empleado: Empleado) -> tuple[str, str]:
    """('Lucía Mora', 'LM'): primer nombre y primer apellido, como en el tablero a mano."""
    nombre = (empleado.nombres.split() or [""])[0]
    apellido = (empleado.apellidos.split() or [""])[0]
    return f"{nombre} {apellido}".strip(), f"{nombre[:1]}{apellido[:1]}".upper()


def venta_mas_alta(anio: int, mes: int):
    """La venta cerrada de mayor valor del mes (mismo mes que la hoja de producción); la primera si hay empate."""
    from apps.ventas.models import EstadoProduccion, RegistroProduccion

    return (RegistroProduccion.objects.filter(hoja__anio=anio, hoja__mes=mes, estado=EstadoProduccion.VENTA_CERRADA)
            .select_related("hoja__vendedor").order_by("-valor_venta", "fecha", "pk").first())


def tablero_ventas(anio: int, mes: int, usuario) -> dict:
    """Todos los vendedores con lo vendido en el mes, del primero al último. Misma fuente que el panel comercial."""
    panel = panel_comercial(anio, mes, usuario)
    maximo = max((f.total_vendido for f in panel.filas), default=Decimal("0"))
    filas = []
    for f in panel.filas:
        nombre, iniciales = _nombre_corto(f.vendedor)
        usuario = f.vendedor.usuario  # Mi perfil: el apodo y la foto también salen en la TV
        if usuario and usuario.apodo:
            nombre, iniciales = usuario.apodo, usuario.iniciales
        filas.append(FilaTablero(fila=f, nombre=nombre, iniciales=iniciales, color=COLORES_TABLERO[f.vendedor.pk % len(COLORES_TABLERO)],
                                 ancho=round(f.total_vendido * 100 / maximo) if maximo else 0, url_foto=usuario.url_foto if usuario else ""))
    mejor = next((f for f in filas if panel.mejor and f.fila is panel.mejor), None)
    venta = venta_mas_alta(anio, mes)
    venta_nombre = _nombre_corto(venta.hoja.vendedor)[0] if venta else ""
    # Podio: los tres primeros que ya vendieron, en el orden en que se dibujan (2.º, 1.º, 3.º).
    con_ventas = [f for f in filas[:3] if f.total_vendido]
    for puesto, f in enumerate(con_ventas, start=1):
        f.puesto = puesto
    podio = sorted(con_ventas, key=lambda f: {2: 0, 1: 1, 3: 2}[f.puesto])
    resto = filas[len(con_ventas):]
    columnas = 1 if len(resto) <= 5 else 2 if len(resto) <= 12 else 3
    return {"filas": filas, "totales": panel.totales, "mejor": mejor, "venta_mas_alta": venta, "venta_nombre": venta_nombre,
            "podio": podio, "resto": resto, "columnas": columnas, "filas_por_columna": -(-len(resto) // columnas) or 1}
