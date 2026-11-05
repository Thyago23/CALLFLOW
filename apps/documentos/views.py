from django.contrib.auth.decorators import login_required, permission_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from .models import Documento


@login_required
@permission_required("documentos.view_documento", raise_exception=True)
def ver_documento(request, pk):
    """
    Los archivos no se publican en una carpeta abierta: solo se entregan a usuarios con permiso.
    """
    documento = get_object_or_404(Documento, pk=pk, eliminado=False)
    try:
        archivo = documento.archivo.open("rb")
    except FileNotFoundError as error:
        raise Http404("El archivo ya no está disponible.") from error
    respuesta = FileResponse(archivo, content_type=documento.tipo_mime, filename=documento.nombre_original)
    respuesta["Content-Disposition"] = respuesta["Content-Disposition"].replace("attachment", "inline")
    respuesta["X-Content-Type-Options"] = "nosniff"
    return respuesta
