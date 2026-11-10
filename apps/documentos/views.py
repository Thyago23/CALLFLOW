from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Count, Max
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.personal.models import Empleado

from . import contratos
from .forms import GenerarContratoForm, PlantillaForm
from .models import Documento, PlantillaContrato


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


# --------------------------------------------------------------------------- plantillas de contrato

@login_required
@permission_required("documentos.view_plantillacontrato", raise_exception=True)
def plantillas(request):
    activas = PlantillaContrato.objects.filter(activa=True).annotate(usos=Count("contratos")).order_by("nombre")
    return render(request, "documentos/plantillas.html", {"plantillas": activas})


def _guardar_plantilla(request, form, anterior=None):
    with transaction.atomic():
        nueva = form.save(commit=False)
        if anterior:
            # Cada edición es una versión nueva: los contratos ya generados conservan su texto original.
            PlantillaContrato.objects.filter(pk=anterior.pk).update(activa=False)
            nueva.pk = None
            nueva.version = (PlantillaContrato.objects.filter(nombre=anterior.nombre).aggregate(v=Max("version"))["v"] or 0) + 1
        nueva.campos_variables = sorted(contratos.variables_en(nueva.contenido))
        nueva.activa = True
        nueva.save()
    messages.success(request, f"Plantilla «{nueva.nombre}» guardada (versión {nueva.version}).")
    return redirect("documentos:plantillas")


def _contexto_editor(form, **extra):
    return {"form": form, "variables": contratos.VARIABLES, **extra}


@login_required
@permission_required("documentos.add_plantillacontrato", raise_exception=True)
def plantilla_nueva(request):
    form = PlantillaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _guardar_plantilla(request, form)
    return render(request, "documentos/plantilla_form.html", _contexto_editor(form))


@login_required
@permission_required("documentos.add_plantillacontrato", raise_exception=True)
def plantilla_editar(request, pk):
    anterior = get_object_or_404(PlantillaContrato, pk=pk, activa=True)
    form = PlantillaForm(request.POST or None, instance=PlantillaContrato(
        nombre=anterior.nombre, tipo_contrato=anterior.tipo_contrato, contenido=anterior.contenido, version=anterior.version,
    ))
    form.instance.pk = anterior.pk  # para que la validación del nombre la reconozca
    if request.method == "POST" and form.is_valid():
        form.instance.pk = None
        return _guardar_plantilla(request, form, anterior=anterior)
    return render(request, "documentos/plantilla_form.html", _contexto_editor(form, plantilla=anterior))


@login_required
@permission_required("documentos.add_plantillacontrato", raise_exception=True)
@require_POST
def plantilla_vista_previa(request):
    """HTMX: muestra cómo queda la plantilla con datos de ejemplo mientras se escribe."""
    contenido = request.POST.get("contenido", "")
    return render(request, "documentos/_vista_previa.html", {
        "texto": contratos.llenar(contenido, contratos.datos_de_ejemplo()),
        "desconocidas": sorted(contratos.variables_desconocidas(contenido)),
        "ejemplo": True,
    })


# --------------------------------------------------------------------------- contratos del empleado
def _form_contrato(request, empleado):
    return GenerarContratoForm(request.POST, empleado=empleado)


@login_required
@permission_required("documentos.add_contratogenerado", raise_exception=True)
@require_POST
def contrato_vista_previa(request, empleado_pk):
    """HTMX: el contrato con los datos reales del empleado, antes de generar el PDF."""
    empleado = get_object_or_404(Empleado.objects.select_related("cargo"), pk=empleado_pk)
    form = _form_contrato(request, empleado)
    if not form.is_valid():
        return render(request, "documentos/_vista_previa.html", {"errores": form.errors})
    plantilla = form.cleaned_data["plantilla"]
    datos = contratos.datos_del_empleado(
        empleado, fecha_inicio=form.cleaned_data["fecha_inicio"], fecha_fin=form.cleaned_data["fecha_fin"],
        salario=form.cleaned_data["salario"],
    )
    return render(request, "documentos/_vista_previa.html", {
        "texto": contratos.llenar(plantilla.contenido, datos),
        "faltantes": contratos.faltantes(plantilla.contenido, datos),
    })


@login_required
@permission_required("documentos.add_contratogenerado", raise_exception=True)
@require_POST
def contrato_generar(request, empleado_pk):
    empleado = get_object_or_404(Empleado.objects.select_related("cargo"), pk=empleado_pk)
    form = _form_contrato(request, empleado)
    destino = reverse("personal:empleado_detalle", args=[empleado.pk]) + "#contratos"
    if not form.is_valid():
        errores = [e for lista in form.errors.values() for e in lista]
        messages.error(request, "No se generó el contrato: " + " ".join(errores))
        return redirect(destino)
    with transaction.atomic():
        contrato = contratos.generar_contrato(
            empleado, form.cleaned_data["plantilla"], fecha_inicio=form.cleaned_data["fecha_inicio"],
            fecha_fin=form.cleaned_data["fecha_fin"], salario=form.cleaned_data["salario"], usuario=request.user,
        )
    faltan = contratos.faltantes(contrato.plantilla.contenido, contrato.datos_utilizados)
    aviso = f" Quedaron espacios en blanco para completar a mano: {', '.join(v.descripcion.lower() for v in faltan)}." if faltan else ""
    messages.success(request, f"Contrato generado y guardado en el expediente.{aviso}")
    return redirect(destino)
