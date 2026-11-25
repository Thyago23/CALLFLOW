"""
Validación y guardado de archivos adjuntos (sección 6: validación de archivos y protección de documentos).

El tipo se decide por el contenido real del archivo (sus primeros bytes), no por la extensión
ni por lo que declara el navegador, que se pueden falsear.
"""
import hashlib

from django.core.exceptions import ValidationError

from .models import TAMANO_MAXIMO, ContratoGenerado, Documento, TipoDocumento

FIRMAS = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def tipo_mime_real(archivo) -> str | None:
    archivo.seek(0)
    cabecera = archivo.read(8)
    archivo.seek(0)
    for firma, mime in FIRMAS:
        if cabecera.startswith(firma):
            return mime
    return None


def validar_archivo(archivo) -> str:
    """Devuelve el tipo MIME real o lanza ValidationError con un mensaje para el usuario."""
    if archivo.size == 0:
        raise ValidationError("El archivo está vacío.", code="vacio")
    if archivo.size > TAMANO_MAXIMO:
        raise ValidationError(f"El archivo pesa más de {TAMANO_MAXIMO // (1024 * 1024)} MB.", code="tamano")
    mime = tipo_mime_real(archivo)
    if mime is None:
        raise ValidationError("Solo se aceptan archivos PDF, JPG o PNG.", code="tipo")
    return mime


def huella(archivo) -> str:
    sha = hashlib.sha256()
    archivo.seek(0)
    for bloque in archivo.chunks():
        sha.update(bloque)
    archivo.seek(0)
    return sha.hexdigest()


def guardar_documento(*, empleado, codigo_tipo: str, archivo, usuario, descripcion: str = "") -> Documento:
    """Guarda el archivo en el expediente del empleado. Debe llamarse dentro de una transacción."""
    mime = validar_archivo(archivo)
    return Documento.objects.create(
        empleado=empleado,
        tipo=TipoDocumento.objects.get(codigo=codigo_tipo),
        archivo=archivo,
        nombre_original=archivo.name[:255],
        tipo_mime=mime,
        tamano_bytes=archivo.size,
        hash_sha256=huella(archivo),
        descripcion=descripcion.strip()[:255],
        subido_por=usuario,
    )


def repetido_en_expediente(empleado, archivo) -> Documento | None:
    """El documento del expediente que tiene exactamente el mismo contenido, si ya se subió antes."""
    return (Documento.objects.filter(empleado=empleado, eliminado=False, hash_sha256=huella(archivo))
            .select_related("tipo").first())


def _usos() -> list[tuple]:
    """Registros que se apoyan en un documento: (modelo, campo que lo apunta, cómo se le explica al usuario)."""
    from apps.asistencia.models import NovedadAsistencia
    from apps.nomina.models import SolicitudPago

    return [
        (ContratoGenerado, "documento_id", "es el PDF de un contrato generado"),
        (SolicitudPago, "factura_id", "es la factura de una solicitud de pago"),
        (NovedadAsistencia, "soporte_id", "es el respaldo de una justificación de asistencia"),
    ]


def en_uso(documento: Documento) -> str:
    """Para qué se usa el documento en otro registro; vacío si no lo usa nadie y se puede quitar."""
    for modelo, campo, motivo in _usos():
        if modelo.objects.filter(**{campo: documento.pk}).exists():
            return motivo
    return ""


def ids_en_uso(documentos) -> set[int]:
    """De una lista de documentos, los que no se pueden quitar porque otro registro se apoya en ellos."""
    ids = [d.pk for d in documentos]
    usados = set()
    for modelo, campo, _ in _usos():
        usados |= set(modelo.objects.filter(**{f"{campo}__in": ids}).values_list(campo, flat=True))
    return usados


def quitar_del_expediente(documento: Documento) -> None:
    """
    Saca el documento del expediente (borrado lógico: el archivo se conserva por la política de retención).
    No se quitan los que respaldan otro registro, para no dejar ese registro sin su archivo.
    """
    motivo = en_uso(documento)
    if motivo:
        raise ValidationError(f"No se puede quitar «{documento.nombre_original}»: {motivo}.")
    documento.eliminado = True
    documento.save(update_fields=["eliminado", "actualizado_en"])
