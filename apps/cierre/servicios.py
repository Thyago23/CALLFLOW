"""
4.4 Módulo de cierre de ventas (SCRUM-48 a SCRUM-59).

Reparto de formularios (SCRUM-50, propuesta a confirmar con la empresa): **cola compartida**.
Los formularios enviados quedan en una cola única, primero los de tarjeta activa y luego por
orden de llegada. Cada encargado *toma* el que va a gestionar; mientras lo tiene, solo él (o
el administrador en su nombre) registra el resultado. Puede devolverlo a la cola y el
administrador puede reasignarlo a otro encargado.

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

from .models import GestionCierre, MetaMensual, MovimientoAsignacion, PreferenciaPanel, ResumenVendedorMes

logger = logging.getLogger(__name__)
Estado = FormularioDAMI.Estado
EN_COLA = [Estado.ENVIADO, Estado.EN_GESTION]

MOTIVOS_NO_CONCRETADA = [
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
    """Encargado de cierre cuya cuenta no está vinculada a un empleado: no puede tomar ni registrar cierres."""
    return usuario.rol == Rol.ENCARGADO_CIERRE and empleado_de(usuario) is None


def numero_tarjeta_visible(usuario, formulario, tarjeta) -> str | None:
    """Número completo, en bloques de 4: solo para el encargado que tiene tomado el DAMI, mientras lo gestiona."""
    if (tarjeta is None or formulario.estado != Estado.EN_GESTION or not es_encargado(usuario)
            or formulario.encargado_asignado_id != empleado_de(usuario).pk):
        return None
    try:
        numero = descifrar(tarjeta.numero_cifrado)
    except InvalidToken:
        logger.error("No se pudo descifrar la tarjeta del formulario %s: revise CLAVE_TARJETAS.", formulario.pk)
        return None
    return " ".join(numero[i:i + 4] for i in range(0, len(numero), 4))


def encargados_activos():
    return (Empleado.objects.filter(usuario__rol=Rol.ENCARGADO_CIERRE, usuario__is_active=True)
            .select_related("usuario").order_by("apellidos", "nombres"))


def vendedores_activos():
    return (Empleado.objects.filter(usuario__rol=Rol.VENDEDOR, estado=Empleado.Estado.ACTIVO)
            .order_by("apellidos", "nombres"))


def url_formulario(formulario) -> str:
    return reverse("cierre:formulario", args=[formulario.pk])


# --------------------------------------------------------------------------- bandeja (SCRUM-48, 49)
def bandeja():
    """Formularios por gestionar: primero los de tarjeta activa, luego del más antiguo al más reciente."""
    return (FormularioDAMI.objects.filter(estado__in=EN_COLA)
            .select_related("cliente", "vendedor", "encargado_asignado", "tarjeta")
            .order_by("prioridad", "enviado_en", "pk"))


def gestionados():
    return (FormularioDAMI.objects.filter(estado__in=[Estado.VENTA_CERRADA, Estado.NO_CONCRETADA])
            .select_related("cliente", "vendedor", "encargado_asignado", "gestion")
            .order_by("-gestion__gestionado_en", "-pk"))


def resumen_bandeja() -> dict:
    cola = FormularioDAMI.objects.filter(estado__in=EN_COLA)
    return {
        "en_cola": cola.count(),
        "sin_asignar": cola.filter(estado=Estado.ENVIADO).count(),
        "prioritarios": cola.filter(prioridad=1).count(),
        "prioritarios_sin_asignar": cola.filter(prioridad=1, estado=Estado.ENVIADO).count(),
    }


# --------------------------------------------------------------------------- asignación (SCRUM-51, 59)
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


@transaction.atomic
def tomar(usuario, formulario, version: int) -> FormularioDAMI:
    if not es_encargado(usuario):
        raise PermissionDenied("Solo los encargados de cierre toman formularios.")
    if formulario.estado != Estado.ENVIADO or formulario.encargado_asignado_id:
        if formulario.encargado_asignado_id:
            raise Conflicto(f"Este formulario ya lo está gestionando {formulario.encargado_asignado}.")
        raise ValidationError("Este formulario ya no está en la cola.")
    encargado = empleado_de(usuario)
    try:
        with transaction.atomic():
            actualizado = _actualizar_con_version(
                formulario, version, Q(estado=Estado.ENVIADO, encargado_asignado__isnull=True),
                estado=Estado.EN_GESTION, encargado_asignado=encargado, asignado_en=timezone.now(),
            )
    except IntegrityError as error:  # p. ej. un encargado que también es el vendedor del DAMI
        raise ValidationError("No puede tomar un formulario que usted mismo registró.") from error
    MovimientoAsignacion.objects.create(formulario=actualizado, accion=MovimientoAsignacion.Accion.TOMAR,
                                        a_encargado=encargado, usuario=usuario)
    _marcar_avisos_atendidos(url_formulario(actualizado))
    return actualizado


@transaction.atomic
def liberar(usuario, formulario, version: int) -> FormularioDAMI:
    """Devuelve el formulario a la cola para que lo tome otro encargado."""
    propio = es_encargado(usuario) and formulario.encargado_asignado_id == empleado_de(usuario).pk
    if not (propio or es_administrador(usuario)):
        raise PermissionDenied("Solo quien tiene el formulario (o el administrador) puede devolverlo a la cola.")
    if formulario.estado != Estado.EN_GESTION:
        raise ValidationError("Este formulario no está en gestión.")
    anterior = formulario.encargado_asignado
    actualizado = _actualizar_con_version(
        formulario, version, Q(estado=Estado.EN_GESTION, encargado_asignado=anterior),
        estado=Estado.ENVIADO, encargado_asignado=None, asignado_en=None,
    )
    MovimientoAsignacion.objects.create(formulario=actualizado, accion=MovimientoAsignacion.Accion.LIBERAR,
                                        de_encargado=anterior, usuario=usuario)
    otros = [e.usuario for e in encargados_activos().exclude(pk=getattr(anterior, "pk", None))]
    _notificar(otros, titulo=f"{actualizado} volvió a la cola de cierre",
               mensaje=f"Lo devolvió {usuario.get_full_name() or usuario.username}.", url=url_formulario(actualizado))
    return actualizado


@transaction.atomic
def reasignar(usuario, formulario, version: int, encargado: Empleado) -> FormularioDAMI:
    if not es_administrador(usuario):
        raise PermissionDenied("Solo el administrador reasigna formularios.")
    if formulario.estado not in EN_COLA:
        raise ValidationError("Solo se reasignan formularios que siguen en la cola.")
    if not encargados_activos().filter(pk=encargado.pk).exists():
        raise ValidationError("Elija un encargado de cierre activo.")
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
        _notificar([encargado.usuario], titulo=f"Se le asignó {actualizado} para cierre",
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
    Registra el resultado. La base de datos actualiza en la misma transacción el DAMI y la hoja
    de producción del vendedor (SCRUM-53): si algo falla, no se guarda nada.
    """
    valor, motivo = _validar_resultado(resultado, valor_venta, motivo.strip())
    bloqueado = (FormularioDAMI.objects.select_for_update(of=("self",)).select_related("encargado_asignado", "vendedor")
                 .get(pk=formulario.pk))
    if bloqueado.version != version:
        raise Conflicto()
    if bloqueado.estado != Estado.EN_GESTION or bloqueado.encargado_asignado is None:
        raise ValidationError("Primero tome el formulario para gestionarlo.")
    propio = es_encargado(usuario) and bloqueado.encargado_asignado_id == empleado_de(usuario).pk
    if not (propio or es_administrador(usuario)):
        raise PermissionDenied(f"Este formulario lo está gestionando {bloqueado.encargado_asignado}.")
    try:
        with transaction.atomic():
            gestion = GestionCierre.objects.create(
                formulario=bloqueado, encargado=bloqueado.encargado_asignado, resultado=resultado,
                valor_venta=valor, motivo_no_concretada=motivo, observaciones=observaciones.strip(),
            )
    except IntegrityError as error:  # la base de datos no admite dos resultados para un mismo formulario
        raise Conflicto("Este formulario ya tiene un resultado registrado.") from error

    vendedor = bloqueado.vendedor
    if vendedor.usuario_id:
        texto = (f"Venta cerrada por ${valor:,.2f}" if valor else f"Venta no concretada: {motivo}")
        _notificar([vendedor.usuario], titulo=f"Resultado de {bloqueado}: {gestion.get_resultado_display().lower()}",
                   mensaje=texto, url=reverse("ventas:hoja"))
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
    """Cambios de estado (trigger de ventas) y movimientos de asignación, en orden."""
    from apps.core.models import Usuario

    estados = list(formulario.historial.all())
    movimientos = list(formulario.movimientos_asignacion.select_related("de_encargado", "a_encargado", "usuario"))
    nombres = {u.pk: u.get_full_name() or u.username for u in Usuario.objects.filter(pk__in={h.usuario_id for h in estados})}
    etiquetas = dict(Estado.choices)
    filas = [{"fecha": h.fecha, "tipo": "estado", "estado": h.estado_nuevo,
              "texto": etiquetas.get(h.estado_nuevo, h.estado_nuevo), "usuario": nombres.get(h.usuario_id, "Sistema")}
             for h in estados]
    for m in movimientos:
        if m.accion == MovimientoAsignacion.Accion.REASIGNAR:
            texto = f"Reasignado de {m.de_encargado or 'la cola'} a {m.a_encargado}"
        elif m.accion == MovimientoAsignacion.Accion.TOMAR:
            texto = f"Lo tomó {m.a_encargado}"
        else:
            texto = f"{m.de_encargado} lo devolvió a la cola"
        filas.append({"fecha": m.fecha, "tipo": "asignacion", "texto": texto,
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
    """`montos`: {vendedor_id: monto}. Crea o cambia la meta; cada cambio lo audita la base de datos."""
    cambios = 0
    for vendedor_id, monto in montos.items():
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
    vendedores |= {v.pk: v for v in Empleado.objects.filter(pk__in=set(resumen) - set(vendedores))}
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
COLORES_TABLERO = ["#ff6b6b", "#ffa94d", "#ffd43b", "#69db7c", "#38d9a9", "#4dabf7", "#9775fa", "#f783ac", "#63e6be", "#74c0fc"]


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
