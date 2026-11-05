"""
Validación y guardado de archivos adjuntos (sección 6: validación de archivos y protección de documentos).

El tipo se decide por el contenido real del archivo (sus primeros bytes), no por la extensión
ni por lo que declara el navegador, que se pueden falsear.
"""
import hashlib

from django.core.exceptions import ValidationError

from .models import TAMANO_MAXIMO, Documento, TipoDocumento

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


def guardar_documento(*, empleado, codigo_tipo: str, archivo, usuario) -> Documento:
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
        subido_por=usuario,
    )
