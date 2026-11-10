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
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacion, Rol, Usuario
from apps.personal.models import Empleado

from . import tarjetas
from .models import (AsignacionBase, BaseComercial, Cliente, ContactoRepetido, FormularioDAMI, HojaProduccion, RegistroBase,
                     RegistroProduccion, SolicitudAyuda, SolicitudBase, TarjetaDAMI)

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
        if not empleado:
            return qs.none()
        return qs.filter(Q(vendedor=empleado) | Q(ayudado_por=empleado), estado=Estado.BORRADOR)
    return qs


def formulario_editable(usuario, formulario) -> bool:
    """Borrador propio, o uno que el usuario está llenando como ayuda para otro asesor."""
    return (formulario.estado == Estado.BORRADOR and es_vendedor(usuario)
            and empleado_de(usuario).pk in (formulario.vendedor_id, formulario.ayudado_por_id)
            and usuario.has_perm("ventas.change_formulariodami"))


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
def guardar_formulario(usuario, datos: dict, formulario: FormularioDAMI | None = None, *, enviar: bool,
                       para=None) -> FormularioDAMI:
    """
    `para`: el vendedor dueño del cliente cuando el usuario llena el DAMI como ayuda (ver `ayuda_aceptada`).
    Entonces el DAMI es de `para` (su hoja de producción y sus ventas) y queda registrado quién lo llenó.
    """
    empleado = empleado_de(usuario)
    if not es_vendedor(usuario):
        raise PermissionDenied("Solo los vendedores registran formularios DAMI.")
    if formulario is not None and not formulario_editable(usuario, formulario):
        raise ValidationError("Este formulario ya no se puede modificar.")

    cliente = guardar_cliente(datos)
    if formulario is None:
        ayuda = para is not None and para.pk != empleado.pk
        formulario = FormularioDAMI(vendedor=para if ayuda else empleado, ayudado_por=empleado if ayuda else None)
    formulario.cliente = cliente
    formulario.base_comercial = datos.get("base_comercial")
    formulario.observaciones = (datos.get("observaciones") or "").strip()
    formulario.producto = datos.get("producto") or ""
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
        mensaje=f"{tarjeta.get_marca_display()} · {tarjeta.banco} · asesor {formulario.vendedor}"
                + (f" (lo llenó {formulario.ayudado_por} como ayuda)" if formulario.ayudado_por_id else ""),
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
    activa = bool(datos.get("activa"))  # lo indica el vendedor en el formulario
    if formulario.tiene_tarjeta_activa != activa:
        formulario.tiene_tarjeta_activa = activa  # la columna "prioridad" la recalcula la base de datos
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
    # Primero, de quién es la fila: a una fila ajena no se le valida nada, se rechaza (403).
    registro = RegistroProduccion.objects.select_for_update().filter(
        pk=registro_id, hoja__vendedor=empleado_de(usuario)).first()
    if registro is None or not es_vendedor(usuario):
        raise PermissionDenied("Esa fila no es de su hoja de producción.")
    aprobacion = hora_aprobacion(texto)
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
        # Los que ya están en otras bases no entran: van a la base de repetidos.
        existentes = _indice_contactos(cedulas={f["cedula"] for f in resultado.filas if f["cedula"] and f["cedula_valida"]},
                                       numeros={n for f in resultado.filas for n in _numeros(f)})
        nuevas, de_otras_bases = [], []
        for fila in resultado.filas:
            original, criterio = _coincidencia(fila, *existentes)
            if original:
                de_otras_bases.append((fila, original, criterio))
            else:
                nuevas.append(fila)
        resumen = resultado.resumen() | {"importadas": len(nuevas), "repetidos_otras_bases": len(de_otras_bases)}
        base = BaseComercial.objects.create(
            nombre=nombre, descripcion=(descripcion or "").strip(), total_registros=len(nuevas), cargada_por=usuario,
            archivo_origen=(getattr(archivo, "name", "") or "")[:255], resumen_carga=resumen)
        creados = RegistroBase.objects.bulk_create([RegistroBase(base=base, **fila) for fila in nuevas], batch_size=1000)
        por_fila = {r.fila: r for r in creados} | {fila["fila"]: original for fila, original, _ in de_otras_bases}
        repetidos = [ContactoRepetido(base=base, motivo=ContactoRepetido.Motivo.OTRA_BASE, criterio=criterio, original=original,
                                      **_solo_datos(fila)) for fila, original, criterio in de_otras_bases]
        # Del mismo archivo: coincide con la primera aparición (o, si esa ya estaba en otra base, con el contacto de esa base).
        repetidos += [ContactoRepetido(base=base, motivo=ContactoRepetido.Motivo.MISMO_ARCHIVO, criterio=fila["criterio"],
                                       original=por_fila.get(fila["repite_fila"]), **_solo_datos(fila))
                      for fila in resultado.repetidas]
        ContactoRepetido.objects.bulk_create(repetidos, batch_size=1000)
        if repetidos:
            _avisar_repetidos(base, len(de_otras_bases), resultado.duplicadas, usuario)
    return base


# --------------------------------------------------------------------------- contactos repetidos
def _numeros(contacto) -> list[str]:
    if isinstance(contacto, dict):
        return [contacto["numero"]] + [n for n in contacto.get("numero_alterno", "").split(" / ") if n]
    return contacto.numeros


def _solo_datos(fila: dict) -> dict:
    return {campo: fila[campo] for campo in ContactoRepetido.CAMPOS}


def _indice_contactos(cedulas, numeros) -> tuple[dict, dict]:
    """
    ({cédula: contacto}, {teléfono: contacto}) de los contactos ya cargados que pueden coincidir
    con estas cédulas o teléfonos (también por sus «otros números»); manda el más antiguo.
    """
    numeros = set(numeros or [])
    # «otros números» se guarda como «0991… / 0982…»: se compara cada teléfono por separado.
    con_alterno = [pk for pk, alternos in RegistroBase.objects.exclude(numero_alterno="").values_list("pk", "numero_alterno")
                   if numeros.intersection(alternos.split(" / "))]
    qs = RegistroBase.objects.filter(Q(cedula__in=cedulas or [], cedula_valida=True) | Q(numero__in=numeros) | Q(pk__in=con_alterno))
    por_cedula, por_numero = {}, {}
    for r in qs.select_related("base", "vendedor").order_by("creado_en", "pk"):
        if r.cedula and r.cedula_valida:
            por_cedula.setdefault(r.cedula, r)
        for n in r.numeros:
            por_numero.setdefault(n, r)
    return por_cedula, por_numero


def _coincidencia(contacto, por_cedula: dict, por_numero: dict):
    """(contacto existente, criterio) con el que coincide, o (None, None)."""
    cedula = contacto["cedula"] if isinstance(contacto, dict) else contacto.cedula
    valida = contacto["cedula_valida"] if isinstance(contacto, dict) else contacto.cedula_valida
    if cedula and valida and cedula in por_cedula:
        return por_cedula[cedula], ContactoRepetido.Criterio.CEDULA
    for n in _numeros(contacto):
        if n in por_numero:
            return por_numero[n], ContactoRepetido.Criterio.NUMERO
    return None, None


def usuarios_gestion_bases():
    """Quienes cargan y reparten bases: administradores y encargados de cierre."""
    return usuarios_con_rol(Rol.ADMINISTRADOR, Rol.ENCARGADO_CIERRE)


def _avisar_repetidos(base, de_otras_bases: int, del_archivo: int, usuario=None, ya_enviados: int = 0) -> None:
    total = de_otras_bases + del_archivo
    partes = []
    if de_otras_bases:
        partes.append(f"{de_otras_bases} ya estaban en otras bases")
    if del_archivo:
        partes.append(f"{del_archivo} estaban repetidos en el mismo archivo")
    mensaje = ("; ".join(partes) + ". No se enviarán a los vendedores hasta que los revise.") if partes else ""
    if ya_enviados:
        mensaje += f" Además, {ya_enviados} repetidos ya estaban enviados a vendedores y se dejaron donde están."
    destinatarios = set(usuarios_gestion_bases())
    if usuario is not None and getattr(usuario, "is_active", False):
        destinatarios.add(usuario)
    _notificar(destinatarios, titulo=f"{total} contacto{'s' if total != 1 else ''} repetido{'s' if total != 1 else ''}"
               + (f" en la base «{base.nombre}»" if base else " en las bases comerciales"),
               mensaje=mensaje.strip(), url=reverse("ventas:repetidos") + (f"?base={base.pk}" if base else ""),
               tipo=Notificacion.Tipo.CONTACTOS_REPETIDOS)


@transaction.atomic
def buscar_repetidos(usuario=None) -> dict:
    """
    Revisa TODAS las bases ya cargadas. El contacto cargado primero se queda en su base; los
    repetidos posteriores que aún no se enviaron a ningún vendedor pasan a la base de repetidos.
    Los que ya tiene un vendedor no se mueven (se cuentan aparte), ni los que alguien ya revisó y
    recuperó a su base por no ser la misma persona.
    Devuelve {"movidos": n, "ya_enviados": n, "por_base": {id de base: n}}.
    """
    recuperados = set(ContactoRepetido.objects.filter(estado=ContactoRepetido.Estado.RECUPERADO)
                      .values_list("base_id", "fila", "numero"))
    por_cedula, por_numero, mover, ya_enviados = {}, {}, [], 0
    for r in RegistroBase.objects.select_for_update().order_by("creado_en", "pk"):
        original, criterio = _coincidencia(r, por_cedula, por_numero)
        if (r.base_id, r.fila, r.numero) in recuperados:
            original = None
        if original is None:
            if r.cedula and r.cedula_valida:
                por_cedula.setdefault(r.cedula, r)
            for n in r.numeros:
                por_numero.setdefault(n, r)
        elif r.vendedor_id:
            ya_enviados += 1
        else:
            mover.append((r, original, criterio))
    por_base = defaultdict(int)
    ContactoRepetido.objects.bulk_create([
        ContactoRepetido(base_id=r.base_id, original=original, criterio=criterio, **r.datos_contacto(),
                         motivo=ContactoRepetido.Motivo.MISMO_ARCHIVO if original.base_id == r.base_id else ContactoRepetido.Motivo.OTRA_BASE)
        for r, original, criterio in mover], batch_size=1000)
    for r, _, _ in mover:
        por_base[r.base_id] += 1
    RegistroBase.objects.filter(pk__in=[r.pk for r, _, _ in mover]).delete()
    for base_id, n in por_base.items():
        BaseComercial.objects.filter(pk=base_id).update(total_registros=F("total_registros") - n)
    if mover:  # solo se avisa cuando hay algo nuevo que revisar
        bases = list(BaseComercial.objects.filter(pk__in=por_base))
        de_otras = sum(1 for r, o, _ in mover if o.base_id != r.base_id)
        _avisar_repetidos(bases[0] if len(bases) == 1 else None, de_otras, len(mover) - de_otras, usuario, ya_enviados)
    return {"movidos": len(mover), "ya_enviados": ya_enviados, "por_base": dict(por_base)}


def repetidos_por_revisar() -> int:
    return ContactoRepetido.objects.filter(estado=ContactoRepetido.Estado.POR_REVISAR).count()


@transaction.atomic
def revisar_repetido(usuario, repetido_id: int, accion: str) -> ContactoRepetido:
    """«descartar»: se queda fuera. «recuperar»: no era la misma persona; entra a su base como contacto libre."""
    repetido = ContactoRepetido.objects.select_for_update().select_related("base").filter(pk=repetido_id).first()
    if repetido is None:
        raise ValidationError("Ese contacto repetido no existe.")
    if repetido.estado != ContactoRepetido.Estado.POR_REVISAR:
        raise ValidationError("Ese contacto ya fue revisado.")
    if accion == "descartar":
        repetido.estado = ContactoRepetido.Estado.DESCARTADO
    elif accion == "recuperar":
        RegistroBase.objects.create(base=repetido.base, **repetido.datos_contacto())
        BaseComercial.objects.filter(pk=repetido.base_id).update(total_registros=F("total_registros") + 1)
        repetido.estado = ContactoRepetido.Estado.RECUPERADO
    else:
        raise ValidationError("Acción no válida.")
    repetido.revisado_por, repetido.revisado_en = usuario, timezone.now()
    repetido.save(update_fields=["estado", "revisado_por", "revisado_en", "actualizado_en"])
    return repetido


@transaction.atomic
def descartar_repetidos(usuario, base=None) -> int:
    """Descarta de una vez todos los repetidos por revisar (de una base o de todas)."""
    qs = ContactoRepetido.objects.filter(estado=ContactoRepetido.Estado.POR_REVISAR)
    if base:
        qs = qs.filter(base=base)
    return qs.update(estado=ContactoRepetido.Estado.DESCARTADO, revisado_por=usuario, revisado_en=timezone.now(),
                     actualizado_en=timezone.now())


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
    """Sus contactos y los de otros asesores en los que aceptó ayudar (se muestran como «cliente de»)."""
    ayudando = SolicitudAyuda.objects.filter(ayudante=empleado, estado=SolicitudAyuda.Estado.ACEPTADA).values("contacto_id")
    return RegistroBase.objects.filter(Q(vendedor=empleado) | Q(pk__in=ayudando)).select_related("base", "vendedor")


@transaction.atomic
def categorizar(usuario, registro_id: int, estado: str) -> RegistroBase:
    """El vendedor marca a su cliente; puede corregirlo o devolverlo a pendiente."""
    if estado not in RegistroBase.Estado.values:
        raise ValidationError("Categoría no válida.")
    empleado = empleado_de(usuario)
    visibles = contactos_del_vendedor(empleado).values("pk") if empleado else RegistroBase.objects.none()
    registro = RegistroBase.objects.select_for_update().filter(pk=registro_id, pk__in=visibles).first()
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


# --------------------------------------------------------------------------- mis estadísticas (vendedor)
@dataclass
class Comparacion:
    """Un indicador del mes en curso frente al mes anterior a la misma fecha y al mes anterior completo."""
    nombre: str
    actual: Decimal
    misma_fecha: Decimal
    anterior_completo: Decimal
    moneda: bool = False

    @property
    def diferencia(self):
        return self.actual - self.misma_fecha

    @property
    def variacion(self):
        """% de cambio frente al mes anterior a la misma fecha (None si entonces no había nada)."""
        return round(self.diferencia * 100 / self.misma_fecha) if self.misma_fecha else None

    @property
    def subio(self) -> bool:
        return self.diferencia > 0

    @property
    def bajo(self) -> bool:
        return self.diferencia < 0


def _produccion_hasta(empleado, anio: int, mes: int, dia: int) -> dict:
    """DAMI enviados, ventas cerradas y monto del vendedor desde el día 1 hasta `dia` (como la hoja de producción)."""
    from django.db.models import Count, Sum

    from apps.reportes.estadisticas import ultimo_dia

    fin = date(anio, mes, min(dia, ultimo_dia(anio, mes).day))
    cerrada = Q(estado="VENTA_CERRADA")
    d = RegistroProduccion.objects.filter(hoja__vendedor=empleado, fecha__range=(date(anio, mes, 1), fin)).aggregate(
        enviados=Count("pk", filter=~Q(estado="ANULADO")), cerradas=Count("pk", filter=cerrada),
        total=Sum("valor_venta", filter=cerrada))
    return {"enviados": d["enviados"], "cerradas": d["cerradas"], "total": d["total"] or Decimal("0")}


def mis_estadisticas(empleado, meses: int = 6, hoy: date | None = None) -> dict:
    """
    Crecimiento del vendedor: sus DAMI sacados (enviados a cierre), ventas cerradas y monto por mes,
    con la misma regla de fechas que la hoja de producción y las estadísticas de reportes.
    """
    from apps.reportes import estadisticas

    hoy = hoy or timezone.localdate()
    est = estadisticas.calcular(hoy.year, hoy.month, vendedor=empleado, hoy=hoy)
    serie = est.serie[-meses:]
    actual, anterior = est.serie[-1], est.serie[-2]
    previo = _produccion_hasta(empleado, anterior.anio, anterior.mes, hoy.day)
    comparaciones = [
        Comparacion("DAMI sacados", Decimal(actual.enviados), Decimal(previo["enviados"]), Decimal(anterior.enviados)),
        Comparacion("Ventas cerradas", Decimal(actual.cerradas), Decimal(previo["cerradas"]), Decimal(anterior.cerradas)),
        Comparacion("Monto vendido", actual.total, previo["total"], anterior.total, moneda=True),
    ]
    con_ventas = [m for m in est.serie if m.total > 0]
    filas = []
    for i, m in enumerate(serie):
        previo_mes = est.serie[len(est.serie) - len(serie) + i - 1] if len(est.serie) - len(serie) + i > 0 else None
        if m is actual:  # el mes en curso, a la misma fecha (no contra un mes completo)
            variacion = comparaciones[2].variacion
        else:
            variacion = round((m.total - previo_mes.total) * 100 / previo_mes.total) if previo_mes and previo_mes.total else None
        filas.append({"mes": m, "en_curso": m is actual, "variacion": variacion})
    return {
        "serie": serie, "filas": list(reversed(filas)), "actual": actual, "anterior": anterior, "comparaciones": comparaciones,
        "mejor_mes": max(con_ventas, key=lambda m: (m.total, m.cerradas)) if con_ventas else None,
        "promedio_3": est.promedio_3, "meses": meses, "hoy": hoy,
        "tiene_datos": any(m.enviados for m in est.serie),
    }


# --------------------------------------------------------------------------- ayuda entre asesores con un contacto
def _nombre(empleado) -> str:
    return f"{empleado.nombres} {empleado.apellidos}".strip()


def asesores_para_ayuda(empleado):
    """A quién se le puede pedir ayuda: los demás vendedores activos."""
    from apps.cierre.servicios import vendedores_activos

    return vendedores_activos().exclude(pk=empleado.pk)


def ayuda_aceptada(empleado, contacto_id) -> SolicitudAyuda | None:
    """La ayuda aceptada por `empleado` para ese contacto (le permite gestionarlo y llenar el DAMI a nombre del dueño)."""
    return (SolicitudAyuda.objects.select_related("solicitante", "contacto")
            .filter(ayudante=empleado, contacto_id=contacto_id, estado=SolicitudAyuda.Estado.ACEPTADA).first())


@transaction.atomic
def pedir_ayuda(usuario, contacto_id: int, ayudante_id, mensaje: str = "") -> SolicitudAyuda:
    from .forms import sin_numero_de_tarjeta

    empleado = empleado_de(usuario)
    if not es_vendedor(usuario):
        raise PermissionDenied("Solo los vendedores piden ayuda con sus contactos.")
    contacto = RegistroBase.objects.select_for_update().filter(pk=contacto_id, vendedor=empleado).first()
    if contacto is None:
        raise PermissionDenied("Ese contacto no es suyo.")
    if contacto.estado == RegistroBase.Estado.DAMI_SACADO:
        raise ValidationError("Ese cliente ya tiene el DAMI sacado.")
    ayudante = asesores_para_ayuda(empleado).filter(pk=ayudante_id if str(ayudante_id).isdigit() else 0).first()
    if ayudante is None:
        raise ValidationError("Elija el asesor al que le pide ayuda.")
    if SolicitudAyuda.objects.filter(contacto=contacto, estado__in=SolicitudAyuda.ACTIVAS).exists():
        raise ValidationError("Ese contacto ya tiene una ayuda pedida.")
    mensaje = sin_numero_de_tarjeta(" ".join((mensaje or "").split()))[:500]
    solicitud = SolicitudAyuda.objects.create(contacto=contacto, solicitante=empleado, ayudante=ayudante, mensaje=mensaje)
    if ayudante.usuario_id:
        _notificar([ayudante.usuario], titulo=f"{_nombre(empleado)} te pide ayuda con un cliente",
                   mensaje=f"{contacto.nombre or contacto.numero} · {contacto.ciudad or contacto.base.nombre}" + (f" — «{mensaje}»" if mensaje else ""),
                   url=reverse("ventas:mis_contactos") + "#ayudas", tipo=Notificacion.Tipo.AYUDA_CONTACTO)
    return solicitud


@transaction.atomic
def responder_ayuda(usuario, solicitud_id: int, aceptar: bool) -> SolicitudAyuda:
    empleado = empleado_de(usuario)
    solicitud = (SolicitudAyuda.objects.select_for_update(of=("self",)).select_related("contacto", "solicitante__usuario")
                 .filter(pk=solicitud_id, ayudante=empleado).first() if empleado else None)
    if solicitud is None or not es_vendedor(usuario):
        raise PermissionDenied("Esa petición de ayuda no es para usted.")
    if solicitud.estado != SolicitudAyuda.Estado.PENDIENTE:
        raise ValidationError("Esa petición ya no está pendiente.")
    solicitud.estado = SolicitudAyuda.Estado.ACEPTADA if aceptar else SolicitudAyuda.Estado.RECHAZADA
    solicitud.respondida_en = timezone.now()
    solicitud.save(update_fields=["estado", "respondida_en", "actualizado_en"])
    cliente = solicitud.contacto.nombre or solicitud.contacto.numero
    if solicitud.solicitante.usuario_id:
        _notificar([solicitud.solicitante.usuario],
                   titulo=f"{_nombre(empleado)} {'aceptó ayudarte' if aceptar else 'no puede ayudarte'} con {cliente}",
                   mensaje="Lo verá como «cliente de» usted; si saca el DAMI, queda a su nombre." if aceptar
                   else "Puede pedirle ayuda a otro asesor.",
                   url=reverse("ventas:mis_contactos"), tipo=Notificacion.Tipo.AYUDA_CONTACTO)
    return solicitud


@transaction.atomic
def cancelar_ayuda(usuario, solicitud_id: int) -> SolicitudAyuda:
    """Quien pidió la ayuda la retira (pendiente o ya aceptada)."""
    empleado = empleado_de(usuario)
    solicitud = (SolicitudAyuda.objects.select_for_update(of=("self",)).select_related("contacto", "ayudante__usuario")
                 .filter(pk=solicitud_id, solicitante=empleado).first() if empleado else None)
    if solicitud is None:
        raise PermissionDenied("Esa petición de ayuda no es suya.")
    if solicitud.estado not in SolicitudAyuda.ACTIVAS:
        raise ValidationError("Esa petición ya no está en curso.")
    solicitud.estado = SolicitudAyuda.Estado.CANCELADA
    solicitud.save(update_fields=["estado", "actualizado_en"])
    if solicitud.ayudante.usuario_id:
        _notificar([solicitud.ayudante.usuario],
                   titulo=f"{_nombre(empleado)} ya no necesita ayuda con {solicitud.contacto.nombre or solicitud.contacto.numero}",
                   url=reverse("ventas:mis_contactos"), tipo=Notificacion.Tipo.AYUDA_CONTACTO)
    return solicitud


def ayudas_de(empleado) -> dict:
    """Para «Mis contactos»: peticiones que le hicieron (pendientes) y la ayuda en curso de cada contacto que ve."""
    pendientes = (SolicitudAyuda.objects.filter(ayudante=empleado, estado=SolicitudAyuda.Estado.PENDIENTE)
                  .select_related("contacto__base", "solicitante").order_by("creado_en"))
    en_curso = {s.contacto_id: s for s in SolicitudAyuda.objects.filter(estado__in=SolicitudAyuda.ACTIVAS)
                .filter(Q(solicitante=empleado) | Q(ayudante=empleado, estado=SolicitudAyuda.Estado.ACEPTADA))
                .select_related("ayudante", "solicitante")}
    return {"pendientes": list(pendientes), "en_curso": en_curso}
