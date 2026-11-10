"""
4.3 Módulo de ventas (SCRUM-38 a SCRUM-47).

Reglas que también cuida la base de datos (triggers de la migración 0002):
- Un DAMI nace en BORRADOR o ENVIADO; al enviarlo se crea solo su fila en la hoja de
  producción del mes; cada cambio de estado queda en el historial con el usuario.
- VENTA_CERRADA / NO_CONCRETADA solo las pone el módulo de cierre.
Aquí: quién puede ver/editar cada formulario, notificaciones y estadísticas.
"""
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol, Usuario
from apps.personal.models import Empleado

from . import tarjetas
from .models import (AsignacionBase, BaseComercial, Cliente, FormularioDAMI, HojaProduccion, RegistroBase, RegistroProduccion,
                     SolicitudBase, TarjetaDAMI)

Estado = FormularioDAMI.Estado
EN_CURSO = [Estado.ENVIADO, Estado.EN_GESTION]


# --------------------------------------------------------------------------- quién es quién
def empleado_de(usuario):
    return getattr(usuario, "empleado", None)


def es_vendedor(usuario) -> bool:
    return usuario.rol == Rol.VENDEDOR and empleado_de(usuario) is not None


def es_administrador(usuario) -> bool:
    return usuario.rol == Rol.ADMINISTRADOR or usuario.is_superuser


def formularios_visibles(usuario):
    """
    El vendedor solo ve sus BORRADORES: una vez enviado, el DAMI ya no se abre; queda
    registrado en su hoja de producción sin datos de la tarjeta. Encargados y
    administradores ven todos (de la tarjeta, solo los últimos 4 dígitos).
    """
    qs = FormularioDAMI.objects.select_related("cliente", "vendedor", "base_comercial", "encargado_asignado")
    if usuario.rol == Rol.VENDEDOR:
        empleado = empleado_de(usuario)
        return qs.filter(vendedor=empleado, estado=Estado.BORRADOR) if empleado else qs.none()
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
def buscar_cliente(cedula: str = "", ruc: str = "") -> Cliente | None:
    if cedula:
        cliente = Cliente.objects.filter(tipo_identificacion=Cliente.TipoIdentificacion.CEDULA, identificacion=cedula).first()
        if cliente:
            return cliente
    if ruc:
        return Cliente.objects.filter(Q(ruc=ruc) | Q(tipo_identificacion=Cliente.TipoIdentificacion.RUC, identificacion=ruc)).first()
    return None


def dami_en_curso(cliente: Cliente, excluir=None):
    qs = FormularioDAMI.objects.filter(cliente=cliente, estado__in=EN_CURSO).select_related("vendedor")
    return qs.exclude(pk=excluir.pk) if excluir else qs


CAMPOS_CLIENTE = ("nombres", "apellidos", "telefono", "telefono_alterno", "telefono3", "email", "direccion", "sector", "ciudad", "referencia")


def guardar_cliente(datos: dict) -> Cliente:
    """
    Reutiliza el cliente si ya existe (misma cédula o RUC) y actualiza sus datos de contacto.
    Identificación principal: la cédula; si solo hay RUC, el RUC.
    """
    cedula, ruc = datos.get("cedula") or "", datos.get("ruc") or ""
    campos = {k: datos.get(k) or "" for k in CAMPOS_CLIENTE} | ({"ruc": ruc} if ruc else {})
    cliente, creado = buscar_cliente(cedula, ruc), False
    if cliente is None:
        tipo = Cliente.TipoIdentificacion.CEDULA if cedula else Cliente.TipoIdentificacion.RUC
        cliente = Cliente.objects.create(tipo_identificacion=tipo, identificacion=cedula or ruc, **campos)
        creado = True
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

    cliente = guardar_cliente(datos)
    formulario = formulario or FormularioDAMI(vendedor=empleado)
    formulario.cliente = cliente
    formulario.base_comercial = datos.get("base_comercial")
    formulario.observaciones = (datos.get("observaciones") or "").strip()
    formulario.save()
    if datos.get("tarjeta"):
        guardar_tarjeta(formulario, datos["tarjeta"])
    if enviar:
        enviar_formulario(usuario, formulario)
    return formulario


@transaction.atomic
def enviar_formulario(usuario, formulario: FormularioDAMI) -> FormularioDAMI:
    """BORRADOR -> ENVIADO. La base de datos crea su fila en la hoja de producción (SCRUM-43)."""
    if not formulario_editable(usuario, formulario):
        raise ValidationError("Solo se pueden enviar los borradores propios.")
    if not TarjetaDAMI.objects.filter(formulario=formulario).exists():
        raise ValidationError("Para enviar el DAMI complete los datos de la tarjeta para realizar el pago.")
    formulario.estado = Estado.ENVIADO
    formulario.enviado_en = timezone.now()
    formulario.save(update_fields=["estado", "enviado_en", "actualizado_en"])
    formulario.refresh_from_db()

    tarjeta = formulario.tarjeta
    _notificar(
        usuarios_con_rol(Rol.ENCARGADO_CIERRE),
        titulo=f"Nuevo DAMI para cierre: {formulario} · {formulario.cliente.identificacion}",
        mensaje=f"{tarjeta.get_marca_display()} · {tarjeta.banco} · asesor {formulario.vendedor}",
        url=reverse("cierre:formulario", args=[formulario.pk]),
    )
    return formulario


def guardar_tarjeta(formulario: FormularioDAMI, datos: dict) -> TarjetaDAMI:
    """Guarda la tarjeta del pago: el número completo cifrado y, a la vista, solo los últimos 4 dígitos."""
    tarjeta = TarjetaDAMI.objects.filter(formulario=formulario).first() or TarjetaDAMI(formulario=formulario)
    tarjeta.marca, tarjeta.banco = datos["marca"], datos["banco"]
    tarjeta.caducidad_mes, tarjeta.caducidad_anio = datos["mes"], datos["anio"]
    if datos.get("numero"):
        tarjeta.ultimos4 = datos["numero"][-4:]
        tarjeta.numero_cifrado = tarjetas.cifrar(datos["numero"])
    tarjeta.save()
    if not formulario.tiene_tarjeta_activa:
        formulario.tiene_tarjeta_activa = True  # la columna "prioridad" la recalcula la base de datos
        formulario.save(update_fields=["tiene_tarjeta_activa", "actualizado_en"])
    return tarjeta


@transaction.atomic
def anular_formulario(usuario, formulario: FormularioDAMI) -> FormularioDAMI:
    if not formulario_editable(usuario, formulario):
        raise ValidationError("Solo se pueden anular los borradores propios.")
    formulario.estado = Estado.ANULADO
    formulario.save(update_fields=["estado", "actualizado_en"])
    return formulario


# --------------------------------------------------------------------------- hoja de producción (SCRUM-43, 44)
FILAS_HOJA_PAPEL = 26  # la hoja impresa trae 26 renglones


def hoja_del_mes(empleado, anio: int, mes: int) -> dict:
    """Hoja con las columnas del formato en papel: N°, fecha, nombre cliente, aprobación, ciudad y valor."""
    hoja = HojaProduccion.objects.filter(vendedor=empleado, anio=anio, mes=mes).first()
    registros = (list(RegistroProduccion.objects.filter(hoja=hoja).select_related("formulario__cliente").order_by("fecha", "pk"))
                 if hoja else [])
    return {
        "hoja": hoja,
        "asesor": f"{empleado.nombres} {empleado.apellidos}".strip(),
        "registros": registros,
        "filas_vacias": range(len(registros) + 1, FILAS_HOJA_PAPEL + 1),
        "totales": totales(registros),
    }


def vendedores_con_hoja():
    """Quien hoy es vendedor o quien alguna vez tuvo hoja (aunque ya no trabaje o haya cambiado de rol)."""
    return Empleado.objects.filter(Q(usuario__rol=Rol.VENDEDOR) | Q(hojas_produccion__isnull=False)).distinct()


def hojas_del_mes(anio: int, mes: int) -> list[dict]:
    """Totales del mes por vendedor para el listado del administrador: vendedores activos y quien tenga hoja ese mes."""
    vendedores = (Empleado.objects
                  .filter(Q(usuario__rol=Rol.VENDEDOR, estado=Empleado.Estado.ACTIVO)
                          | Q(hojas_produccion__anio=anio, hojas_produccion__mes=mes))
                  .distinct().order_by("apellidos", "nombres"))
    por_vendedor = defaultdict(list)
    for registro in RegistroProduccion.objects.filter(hoja__anio=anio, hoja__mes=mes).select_related("hoja"):
        por_vendedor[registro.hoja.vendedor_id].append(registro)
    return [{"vendedor": v, "totales": totales(por_vendedor[v.pk])} for v in vendedores]


def hora_aprobacion(texto: str) -> str:
    """
    La aprobación es la hora en que terminó la llamada. Acepta «09:18», «9:18», «0918» o «918»
    y la guarda como 4 dígitos («0918»). Vacío = sin aprobación.
    """
    limpio = re.sub(r"\s+", "", texto or "")
    if not limpio:
        return ""
    coincide = re.fullmatch(r"(\d{1,2}):(\d{2})", limpio) or re.fullmatch(r"(\d{1,2})(\d{2})", limpio)
    if not coincide:
        raise ValidationError("Escriba una hora válida en formato HH:MM (ej.: 09:18).")
    hora, minuto = int(coincide.group(1)), int(coincide.group(2))
    if hora > 23 or minuto > 59:
        raise ValidationError("Esa hora no existe: use de 00:00 a 23:59.")
    return f"{hora:02d}{minuto:02d}"


@transaction.atomic
def guardar_aprobacion(usuario, registro_id: int, texto: str) -> RegistroProduccion:
    """
    El vendedor escribe la aprobación (la hora en que terminó la llamada) en una fila de su
    propia hoja (solo dígitos). Una vez guardada queda fija: ya no se puede cambiar ni borrar.
    """
    aprobacion = hora_aprobacion(texto)
    registro = RegistroProduccion.objects.select_for_update().filter(
        pk=registro_id, hoja__vendedor=empleado_de(usuario)).first()
    if registro is None or not es_vendedor(usuario):
        raise PermissionDenied("Esa fila no es de su hoja de producción.")
    if registro.estado == "ANULADO":
        raise ValidationError("El formulario está anulado.")
    if registro.aprobacion and registro.aprobacion != aprobacion:
        raise ValidationError("La aprobación ya fue registrada y no se puede modificar.")
    if registro.aprobacion != aprobacion:
        registro.aprobacion = aprobacion
        registro.save(update_fields=["aprobacion", "actualizado_en"])
    return registro


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
    hora = timezone.localtime(solicitud.creado_en).strftime("%d/%m/%Y %H:%M")
    _notificar(
        usuarios_con_rol(Rol.ENCARGADO_CIERRE),
        titulo=f"{empleado} solicita una nueva base comercial",
        mensaje=f"{hora} · {solicitud.mensaje[:280]}" if solicitud.mensaje else hora,
        url=url_solicitud(solicitud), tipo=Notificacion.Tipo.SOLICITUD_BASE,
    )
    return solicitud


def url_solicitud(solicitud) -> str:
    """Enlace directo a la solicitud en la lista de pendientes (SCRUM-60)."""
    return f"{reverse('ventas:bases')}#solicitud-{solicitud.pk}"


def _solicitud_resuelta(solicitud) -> None:
    """Cuando un encargado la atiende o rechaza, el aviso deja de estar pendiente para todos (SCRUM-60)."""
    Notificacion.objects.filter(tipo=Notificacion.Tipo.SOLICITUD_BASE, url=url_solicitud(solicitud), leida=False).update(
        leida=True, leida_en=timezone.now())


def solicitudes_pendientes() -> int:
    return SolicitudBase.objects.filter(estado=SolicitudBase.Estado.PENDIENTE).count()


def _usuario_del_vendedor(solicitud):
    usuario = solicitud.vendedor.usuario
    return [usuario] if usuario and usuario.is_active else []


@transaction.atomic
def atender_solicitud(solicitud: SolicitudBase, base: BaseComercial, usuario, respuesta: str = "",
                      cantidad: int | None = None) -> SolicitudBase:
    solicitud = SolicitudBase.objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.estado != SolicitudBase.Estado.PENDIENTE:
        raise ValidationError("Esta solicitud ya fue respondida.")
    if not base.activa:
        raise ValidationError("La base elegida está inactiva.")
    ya_asignada = AsignacionBase.objects.filter(base=base, vendedor=solicitud.vendedor).exists()
    if ya_asignada and not RegistroBase.objects.filter(base=base, vendedor__isnull=True).exists():
        raise ValidationError(f"{solicitud.vendedor} ya tiene asignada la base «{base.nombre}». Elija otra.")
    if not ya_asignada:
        AsignacionBase.objects.create(base=base, vendedor=solicitud.vendedor, asignada_por=usuario)
    recibidos = repartir(base, [solicitud.vendedor], cantidad).get(solicitud.vendedor.pk, 0)
    solicitud.estado = SolicitudBase.Estado.ATENDIDA
    solicitud.base_asignada = base
    solicitud.respuesta = respuesta.strip()
    solicitud.atendida_por = usuario
    solicitud.atendida_en = timezone.now()
    solicitud.save()
    _solicitud_resuelta(solicitud)
    _notificar(_usuario_del_vendedor(solicitud),
               titulo=f"Se le asignó la base «{base.nombre}»" + (f": {recibidos} contactos" if recibidos else ""),
               mensaje=solicitud.respuesta,
               url=reverse("ventas:mis_contactos") + f"?base={base.pk}" if recibidos else reverse("ventas:bases"),
               tipo=Notificacion.Tipo.SOLICITUD_BASE)
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
    _solicitud_resuelta(solicitud)
    _notificar(_usuario_del_vendedor(solicitud), titulo="Su solicitud de base fue rechazada",
               mensaje=solicitud.respuesta, url=reverse("ventas:bases"), tipo=Notificacion.Tipo.SOLICITUD_BASE)
    return solicitud


@transaction.atomic
def asignar_base(base: BaseComercial, vendedores, usuario, cantidad: int | None = None) -> int:
    """Asigna la base y reparte sus contactos libres entre los vendedores (SCRUM-94). Devuelve cuántos vendedores son nuevos."""
    nuevos = [v for v in vendedores if not AsignacionBase.objects.filter(base=base, vendedor=v).exists()]
    AsignacionBase.objects.bulk_create([AsignacionBase(base=base, vendedor=v, asignada_por=usuario) for v in nuevos])
    repartidos = repartir(base, list(vendedores), cantidad)
    for v in vendedores:
        if v.usuario_id and (v in nuevos or repartidos.get(v.pk)):
            n = repartidos.get(v.pk, 0)
            _notificar([v.usuario], titulo=f"Se le asignó la base «{base.nombre}»" + (f": {n} contactos" if n else ""),
                       mensaje="Categorícelos en Bases comerciales → Mis contactos." if n else "",
                       url=reverse("ventas:mis_contactos") + (f"?base={base.pk}" if n else ""), tipo=Notificacion.Tipo.SOLICITUD_BASE)
    return len(nuevos)


# --------------------------------------------------------------------------- contactos de las bases (SCRUM-93 a 97)
def importar_base(nombre: str, descripcion: str, archivo, usuario) -> BaseComercial:
    """Lee el Excel, lo deja en el formato de la empresa y guarda la base con sus contactos."""
    from . import bases_excel

    nombre = " ".join((nombre or "").split())
    if not nombre:
        raise ValidationError("Escriba un nombre para la base.")
    if BaseComercial.objects.filter(nombre__iexact=nombre).exists():
        raise ValidationError("Ya existe una base con ese nombre.")
    resultado = bases_excel.formatear(archivo)
    with transaction.atomic():
        base = BaseComercial.objects.create(
            nombre=nombre, descripcion=(descripcion or "").strip(), total_registros=resultado.importadas, cargada_por=usuario,
            archivo_origen=(getattr(archivo, "name", "") or "")[:255], resumen_carga=resultado.resumen())
        RegistroBase.objects.bulk_create([RegistroBase(base=base, **fila) for fila in resultado.filas], batch_size=1000)
    return base


def repartir(base: BaseComercial, vendedores: list, cantidad: int | None = None) -> dict:
    """
    Envía los contactos aún sin vendedor, en el orden del archivo: `cantidad` a cada uno, o todos
    en partes iguales. Devuelve {id del vendedor: contactos recibidos}.
    """
    if not vendedores:
        return {}
    libres = list(RegistroBase.objects.select_for_update(skip_locked=True).filter(base=base, vendedor__isnull=True)
                  .order_by("fila", "pk").values_list("pk", flat=True))
    if cantidad:
        libres = libres[:cantidad * len(vendedores)]
    por_vendedor, resto = divmod(len(libres), len(vendedores))
    ahora, inicio, recibidos = timezone.now(), 0, {}
    for i, vendedor in enumerate(vendedores):
        tope = min(cantidad, len(libres) - inicio) if cantidad else por_vendedor + (1 if i < resto else 0)
        ids = libres[inicio:inicio + tope]
        inicio += tope
        if ids:
            RegistroBase.objects.filter(pk__in=ids).update(vendedor=vendedor, asignado_en=ahora)
            recibidos[vendedor.pk] = len(ids)
    return recibidos


ESTADOS_GESTION = [RegistroBase.Estado.NO_CONTESTO, RegistroBase.Estado.NO_QUIERE, RegistroBase.Estado.DAMI_SACADO]


def datos_dami_de_contacto(contacto: RegistroBase) -> dict:
    """
    Datos para prellenar un DAMI desde un contacto de la base. «Nombre y apellido» viene en una
    sola columna: se separa a la mitad (el vendedor puede corregirlo).
    """
    palabras = contacto.nombre.split()
    corte = (len(palabras) + 1) // 2 if len(palabras) != 3 else 1
    return {"cedula": contacto.cedula if len(contacto.cedula) == 10 else "", "nombres": " ".join(palabras[:corte]),
            "apellidos": " ".join(palabras[corte:]), "telefono": contacto.numero,
            "telefono_alterno": contacto.numero_alterno.split(" / ")[0] if contacto.numero_alterno else "",
            "ciudad": contacto.ciudad, "direccion": contacto.direccion}


def contactos_del_vendedor(empleado):
    return RegistroBase.objects.filter(vendedor=empleado).select_related("base")


@transaction.atomic
def categorizar(usuario, registro_id: int, estado: str) -> RegistroBase:
    """El vendedor marca a su cliente; puede corregirlo o devolverlo a pendiente."""
    if estado not in RegistroBase.Estado.values:
        raise ValidationError("Categoría no válida.")
    registro = RegistroBase.objects.select_for_update().filter(pk=registro_id, vendedor=empleado_de(usuario)).first()
    if registro is None or not es_vendedor(usuario):
        raise PermissionDenied("Ese contacto no es suyo.")
    registro.estado = estado
    if estado == RegistroBase.Estado.PENDIENTE:
        registro.gestionado_en = None
    else:
        registro.gestionado_en = timezone.now()
        registro.intentos += 1
    registro.save(update_fields=["estado", "gestionado_en", "intentos", "actualizado_en"])
    return registro


def _conteos(prefijo: str = "") -> dict:
    """Agregados de contactos: enviados, pendientes y cada categoría."""
    from django.db.models import Count

    E = RegistroBase.Estado
    filtro = {f"{prefijo}vendedor__isnull": False} if prefijo else {"vendedor__isnull": False}
    return {
        "enviados": Count("pk", filter=Q(**filtro)),
        "pendientes": Count("pk", filter=Q(**{f"{prefijo}estado": E.PENDIENTE}) & Q(**filtro)),
        "no_contesto": Count("pk", filter=Q(**{f"{prefijo}estado": E.NO_CONTESTO})),
        "no_quiere": Count("pk", filter=Q(**{f"{prefijo}estado": E.NO_QUIERE})),
        "dami_sacado": Count("pk", filter=Q(**{f"{prefijo}estado": E.DAMI_SACADO})),
    }


def _completar(fila: dict) -> dict:
    fila["gestionados"] = fila["no_contesto"] + fila["no_quiere"] + fila["dami_sacado"]
    fila["avance"] = round(fila["gestionados"] * 100 / fila["enviados"]) if fila["enviados"] else None
    fila["efectividad"] = Decimal(fila["dami_sacado"] * 100) / fila["gestionados"] if fila["gestionados"] else None
    return fila


def seguimiento(base=None, vendedor=None) -> list[dict]:
    """Por vendedor y base (SCRUM-96): enviados, gestionados, pendientes («faltan») y cada categoría."""
    from apps.personal.models import Empleado

    qs = RegistroBase.objects.filter(vendedor__isnull=False)
    if base:
        qs = qs.filter(base=base)
    if vendedor:
        qs = qs.filter(vendedor=vendedor)
    filas = [_completar(f) for f in qs.values("vendedor", "base").annotate(**_conteos())]
    vendedores = {e.pk: e for e in Empleado.objects.filter(pk__in={f["vendedor"] for f in filas})}
    bases = {b.pk: b for b in BaseComercial.objects.filter(pk__in={f["base"] for f in filas})}
    for f in filas:
        f["vendedor"], f["base"] = vendedores[f["vendedor"]], bases[f["base"]]
    return sorted(filas, key=lambda f: (-f["pendientes"], str(f["vendedor"]), f["base"].nombre))


def efectividad_bases(base=None) -> list[dict]:
    """Por base (SCRUM-97): contactos, asignados, gestionados, categorías y efectividad."""
    from django.db.models import Count

    qs = BaseComercial.objects.all()
    if base:
        qs = qs.filter(pk=base.pk)
    filas = []
    for b in qs.annotate(contactos=Count("registros"), **_conteos("registros__")).order_by("-creado_en"):
        fila = _completar({"base": b, "contactos": b.contactos, "enviados": b.enviados, "pendientes": b.pendientes,
                           "no_contesto": b.no_contesto, "no_quiere": b.no_quiere, "dami_sacado": b.dami_sacado})
        fila["sin_asignar"] = b.contactos - b.enviados
        fila["efectividad_total"] = Decimal(b.dami_sacado * 100) / b.contactos if b.contactos else None
        filas.append(fila)
    return filas
