"""Lógica de la mensajería interna (las vistas solo la llaman)."""
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.utils import timezone

from apps.core.models import Usuario

from .acceso import ROLES_MENSAJERIA
from .models import Conversacion, Mensaje, Participante

MAX_TEXTO = 4000


def _mensaje_bd(error: DatabaseError) -> str:
    texto = str(getattr(error, "__cause__", None) or error).split("\n")[0]
    return texto.removeprefix("ERROR:").strip()


def contactos(usuario):
    """Con quién puede hablar: cuentas activas de TI, administración y supervisión (sin asesores)."""
    return (Usuario.objects.filter(is_active=True, rol__in=ROLES_MENSAJERIA).exclude(pk=usuario.pk)
            .order_by("first_name", "last_name", "username"))


def conversaciones_de(usuario):
    """Las conversaciones donde participa, la de actividad más reciente primero, con cuántos mensajes no leyó."""
    mio = Participante.objects.filter(conversacion=OuterRef("pk"), usuario=usuario)
    ultimo = Mensaje.objects.filter(conversacion=OuterRef("pk")).order_by("-creado_en", "-pk")
    return (
        Conversacion.objects.filter(participantes__usuario=usuario, participantes__activo=True)
        .annotate(
            leido_hasta=Subquery(mio.values("ultimo_leido_en")[:1]),
            ultimo_texto=Subquery(ultimo.values("texto")[:1]),
            ultimo_adjunto=Subquery(ultimo.values("adjunto_nombre")[:1]),
            ultimo_eliminado=Subquery(ultimo.values("eliminado")[:1]),
            ultimo_autor=Subquery(ultimo.values("autor_id")[:1]),
            no_leidos=Count("mensajes", filter=Q(mensajes__eliminado=False) & ~Q(mensajes__autor=usuario)
                            & (Q(mensajes__creado_en__gt=F("leido_hasta")) | Q(leido_hasta__isnull=True))),
        )
        .order_by(F("ultimo_mensaje_en").desc(nulls_last=True), "-creado_en")
        .prefetch_related("participantes__usuario")
    )


def sin_leer(usuario) -> int:
    return sum(c.no_leidos for c in conversaciones_de(usuario))


def conversacion_de(usuario, pk) -> Conversacion:
    """La conversación solo si participa en ella (si no, como si no existiera)."""
    return Conversacion.objects.get(pk=pk, participantes__usuario=usuario, participantes__activo=True)


def titulo_para(conversacion, usuario) -> str:
    if conversacion.es_grupo:
        return conversacion.nombre
    otro = next((p.usuario for p in conversacion.participantes.all() if p.usuario_id != usuario.pk), None)
    return otro.get_full_name() or otro.username if otro else "Conversación"


def otro_de(conversacion, usuario):
    return next((p.usuario for p in conversacion.participantes.all() if p.usuario_id != usuario.pk), None)


@transaction.atomic
def conversacion_directa(usuario, otro) -> Conversacion:
    """La única conversación directa entre las dos personas (la crea si no existe)."""
    if otro.pk == usuario.pk:
        raise ValidationError("Elija a otra persona.")
    clave = "-".join(str(i) for i in sorted((usuario.pk, otro.pk)))
    conversacion, creada = Conversacion.objects.get_or_create(
        clave_directa=clave, defaults={"tipo": Conversacion.Tipo.DIRECTA, "creada_por": usuario})
    if creada:
        try:
            Participante.objects.bulk_create([Participante(conversacion=conversacion, usuario=usuario),
                                              Participante(conversacion=conversacion, usuario=otro)])
        except DatabaseError as error:
            raise ValidationError(_mensaje_bd(error)) from error
    return conversacion


def crear_grupo(usuario, nombre: str, miembros) -> Conversacion:
    nombre = " ".join(nombre.split())[:80]
    if not nombre:
        raise ValidationError("Póngale un nombre al grupo.")
    miembros = [m for m in miembros if m.pk != usuario.pk]
    if not miembros:
        raise ValidationError("Elija al menos a una persona para el grupo.")
    try:
        with transaction.atomic():
            grupo = Conversacion.objects.create(tipo=Conversacion.Tipo.GRUPO, nombre=nombre, creada_por=usuario)
            Participante.objects.bulk_create([Participante(conversacion=grupo, usuario=u) for u in [usuario, *miembros]])
    except DatabaseError as error:
        raise ValidationError(_mensaje_bd(error)) from error
    return grupo


def agregar_miembros(usuario, grupo, miembros) -> int:
    if not grupo.es_grupo:
        raise ValidationError("Solo se agregan personas a los grupos.")
    agregados = 0
    try:
        with transaction.atomic():
            for miembro in miembros:
                participante, creado = Participante.objects.get_or_create(conversacion=grupo, usuario=miembro)
                if not creado and not participante.activo:  # había salido: vuelve
                    participante.activo = True
                    participante.save(update_fields=["activo"])
                    creado = True
                agregados += int(creado)
    except DatabaseError as error:
        raise ValidationError(_mensaje_bd(error)) from error
    return agregados


def salir(usuario, grupo):
    if not grupo.es_grupo:
        raise ValidationError("De una conversación directa no se sale.")
    Participante.objects.filter(conversacion=grupo, usuario=usuario).update(activo=False)


def enviar(usuario, conversacion, texto: str = "", archivo=None) -> Mensaje:
    from apps.documentos import archivos as validar

    texto = (texto or "").strip()
    if len(texto) > MAX_TEXTO:
        raise ValidationError(f"El mensaje es muy largo (máximo {MAX_TEXTO} caracteres).")
    datos = {"texto": texto}
    if archivo:
        try:
            tipo = validar.validar_archivo(archivo)  # tipo por el contenido real, no por el nombre
        except ValidationError as error:
            raise ValidationError(f"Adjunto: {' '.join(error.messages)}") from error
        datos |= {"adjunto": archivo, "adjunto_nombre": archivo.name[:255], "adjunto_tipo": tipo}
    if not texto and not archivo:
        raise ValidationError("Escriba un mensaje o adjunte un archivo.")
    try:
        with transaction.atomic():
            mensaje = Mensaje.objects.create(conversacion=conversacion, autor=usuario, **datos)
            marcar_leida(usuario, conversacion)
            Participante.objects.filter(conversacion=conversacion, usuario=usuario).update(escribiendo_hasta=None)
    except DatabaseError as error:
        raise ValidationError(_mensaje_bd(error)) from error
    mensaje.refresh_from_db(fields=["creado_en"])
    return mensaje


def eliminar(usuario, mensaje):
    if mensaje.autor_id != usuario.pk:
        raise ValidationError("Solo quien escribió el mensaje puede eliminarlo.")
    if mensaje.eliminado:
        return mensaje
    archivo = mensaje.adjunto.name if mensaje.adjunto else ""
    mensaje.eliminado, mensaje.eliminado_en, mensaje.texto, mensaje.adjunto = True, timezone.now(), "", ""
    mensaje.save(update_fields=["eliminado", "eliminado_en", "texto", "adjunto"])
    if archivo:
        transaction.on_commit(lambda: mensaje.adjunto.storage.delete(archivo))
    return mensaje


def marcar_leida(usuario, conversacion):
    Participante.objects.filter(conversacion=conversacion, usuario=usuario).update(ultimo_leido_en=timezone.now())


def mensajes(conversacion, despues_de: int | None = None, limite: int = 200):
    qs = conversacion.mensajes.select_related("autor")
    if despues_de:
        return list(qs.filter(pk__gt=despues_de))
    ultimos = list(qs.order_by("-creado_en", "-pk")[:limite])
    return ultimos[::-1]



# --------------------------------------------------------------------------- tipo Messenger: en línea, escribiendo y visto
MINUTOS_EN_LINEA = 3
SEGUNDOS_ESCRIBIENDO = 6


def actividad(usuarios) -> dict:
    """{id: última actividad} según las sesiones abiertas que registra el módulo de TI."""
    from django.db.models import Max

    from apps.ti.models import SesionEquipo

    ids = [u.pk for u in usuarios]
    filas = (SesionEquipo.objects.filter(usuario_id__in=ids, fin__isnull=True)
             .values("usuario_id").annotate(ultima=Max("ultima_actividad")))
    return {f["usuario_id"]: f["ultima"] for f in filas}


def presencia(ultima) -> tuple[bool, str]:
    """(en línea, texto): «En línea», «Activo hace 12 min», «Activo hace 2 h» o «Desconectado»."""
    if ultima is None:
        return False, "Desconectado"
    minutos = int((timezone.now() - ultima).total_seconds() // 60)
    if minutos < MINUTOS_EN_LINEA:
        return True, "En línea"
    if minutos < 60:
        return False, f"Activo hace {minutos} min"
    if minutos < 24 * 60:
        return False, f"Activo hace {minutos // 60} h"
    return False, "Desconectado"


def anotar_presencia(usuarios):
    """Agrega .en_linea y .estado_conexion a cada usuario."""
    usuarios = list(usuarios)
    ultimas = actividad(usuarios)
    for u in usuarios:
        u.en_linea, u.estado_conexion = presencia(ultimas.get(u.pk))
    return usuarios


def marcar_escribiendo(usuario, conversacion):
    Participante.objects.filter(conversacion=conversacion, usuario=usuario).update(
        escribiendo_hasta=timezone.now() + timezone.timedelta(seconds=SEGUNDOS_ESCRIBIENDO))


def escribiendo(conversacion, usuario) -> list:
    """Quiénes (otros) están escribiendo ahora en la conversación."""
    return [p.usuario for p in conversacion.participantes.select_related("usuario")
            .filter(activo=True, escribiendo_hasta__gt=timezone.now()).exclude(usuario=usuario)]


def visto(conversacion, usuario) -> str:
    """Debajo de mi último mensaje: «Visto», «Visto por N», «Enviado» o nada si el último no es mío."""
    ultimo = conversacion.mensajes.order_by("-creado_en", "-pk").first()
    if ultimo is None or ultimo.autor_id != usuario.pk or ultimo.eliminado:
        return ""
    otros = [p for p in conversacion.participantes.all() if p.activo and p.usuario_id != usuario.pk]
    leyeron = sum(1 for p in otros if p.ultimo_leido_en and p.ultimo_leido_en >= ultimo.creado_en)
    if not leyeron:
        return "Enviado"
    if not conversacion.es_grupo or leyeron == len(otros):
        return "Visto" if not conversacion.es_grupo else "Visto por todos"
    return f"Visto por {leyeron}"


# --------------------------------------------------------------------------- editar
def editar(usuario, mensaje, texto: str) -> Mensaje:
    """Corrige el texto de un mensaje propio. Queda «Editado» y la base guarda la versión anterior."""
    texto = (texto or "").strip()
    if mensaje.autor_id != usuario.pk:
        raise ValidationError("Solo quien escribió el mensaje puede editarlo.")
    if mensaje.eliminado:
        raise ValidationError("Un mensaje eliminado no se edita.")
    if not texto and not mensaje.adjunto:
        raise ValidationError("El mensaje no puede quedar vacío; si no lo quiere, elimínelo.")
    if len(texto) > MAX_TEXTO:
        raise ValidationError(f"El mensaje es muy largo (máximo {MAX_TEXTO} caracteres).")
    if texto == mensaje.texto:
        return mensaje
    mensaje.texto, mensaje.editado_en = texto, timezone.now()
    try:
        with transaction.atomic():
            mensaje.save(update_fields=["texto", "editado_en"])
    except DatabaseError as error:
        mensaje.refresh_from_db()
        raise ValidationError(_mensaje_bd(error)) from error
    return mensaje


def cambiados(conversacion, desde, hasta_id: int):
    """Mensajes ya mostrados que se editaron o eliminaron después de `desde` (para actualizarlos en pantalla)."""
    return list(conversacion.mensajes.select_related("autor")
                .filter(pk__lte=hasta_id).filter(Q(editado_en__gt=desde) | Q(eliminado_en__gt=desde)))
