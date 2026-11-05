"""Guardado seguro de archivos adjuntos (PDF, JPG, PNG)."""
import hashlib

from django.core.exceptions import ValidationError

from .models import TAMANO_MAXIMO, Documento, TipoDocumento

# Firma de los primeros bytes de cada formato: no se confía en la extensión ni en lo que diga el navegador.
_FIRMAS = [
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
]


def detectar_tipo(cabecera: bytes) -> str | None:
    for firma, tipo in _FIRMAS:
        if cabecera.startswith(firma):
            return tipo
    return None


def guardar_documento(archivo, *, tipo_codigo: str, empleado=None, usuario=None, descripcion: str = "") -> Documento:
    """
    Valida y guarda un archivo subido. Si el mismo archivo ya está en el expediente del
    empleado, devuelve el existente en vez de duplicarlo.
    """
    if archivo.size == 0:
        raise ValidationError("El archivo está vacío.")
    if archivo.size > TAMANO_MAXIMO:
        raise ValidationError(f"El archivo supera el máximo de {TAMANO_MAXIMO // (1024 * 1024)} MB.")

    archivo.seek(0)
    tipo_mime = detectar_tipo(archivo.read(16))
    if tipo_mime is None:
        raise ValidationError("Solo se aceptan archivos PDF, JPG o PNG.")

    archivo.seek(0)
    huella = hashlib.sha256()
    for bloque in archivo.chunks():
        huella.update(bloque)
    hash_hex = huella.hexdigest()
    archivo.seek(0)

    existente = Documento.objects.filter(empleado=empleado, hash_sha256=hash_hex, eliminado=False).first()
    if existente:
        return existente

    return Documento.objects.create(
        empleado=empleado,
        tipo=TipoDocumento.objects.get(codigo=tipo_codigo),
        archivo=archivo,
        nombre_original=archivo.name[:255],
        tipo_mime=tipo_mime,
        tamano_bytes=archivo.size,
        hash_sha256=hash_hex,
        descripcion=descripcion[:255],
        subido_por=usuario,
    )
