from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import servicios
from .forms import MaterialForm, MovimientoForm
from .models import CategoriaMaterial, Material, MovimientoInventario


def _es_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


# --------------------------------------------------------------------------- materiales (SCRUM-25 / 27)
@login_required
@permission_required("inventario.view_material", raise_exception=True)
def materiales(request):
    qs = Material.objects.select_related("categoria").order_by("-activo", "nombre")
    texto = request.GET.get("q", "").strip()
    if texto:
        qs = qs.filter(Q(nombre__icontains=texto) | Q(codigo__icontains=texto) | Q(descripcion__icontains=texto))
    categoria = request.GET.get("categoria", "")
    if categoria.isdigit():
        qs = qs.filter(categoria_id=int(categoria))
    estado = request.GET.get("estado", "")
    if estado == "bajo":
        qs = qs.filter(pk__in=servicios.materiales_stock_bajo())
    elif estado == "inactivos":
        qs = qs.filter(activo=False)
    elif estado == "activos":
        qs = qs.filter(activo=True)

    contexto = {
        "materiales": qs, "q": texto, "categoria": categoria, "estado": estado,
        "categorias": CategoriaMaterial.objects.order_by("nombre"),
        "bajos": servicios.materiales_stock_bajo().order_by("nombre"),
    }
    plantilla = "inventario/_materiales_tabla.html" if _es_htmx(request) else "inventario/materiales.html"
    return render(request, plantilla, contexto)


def _guardar_material(request, form, nuevo: bool):
    datos = form.cleaned_data
    material = form.save(commit=False)
    with transaction.atomic():
        if not datos.get("categoria") and datos.get("categoria_nueva"):
            material.categoria = servicios.categoria_por_nombre(datos["categoria_nueva"])
        if nuevo:
            servicios.crear_material(material, datos.get("stock_inicial"), request.user)
        else:
            material.save()
    return material


@login_required
@permission_required("inventario.add_material", raise_exception=True)
def material_crear(request):
    form = MaterialForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            material = _guardar_material(request, form, nuevo=True)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, f"Material «{material.nombre}» registrado con el código {material.codigo}.")
            return redirect("inventario:material_detalle", pk=material.pk)
    return render(request, "inventario/material_form.html", {"form": form, "nuevo": True})


@login_required
@permission_required("inventario.change_material", raise_exception=True)
def material_editar(request, pk):
    material = get_object_or_404(Material, pk=pk)
    form = MaterialForm(request.POST or None, instance=material)
    if request.method == "POST" and form.is_valid():
        _guardar_material(request, form, nuevo=False)
        messages.success(request, f"Material «{material.nombre}» actualizado.")
        return redirect("inventario:material_detalle", pk=material.pk)
    return render(request, "inventario/material_form.html", {"form": form, "nuevo": False, "material": material})


@login_required
@permission_required("inventario.view_material", raise_exception=True)
def material_detalle(request, pk):
    material = get_object_or_404(Material.objects.select_related("categoria"), pk=pk)
    movimientos = material.movimientos.select_related("responsable").order_by("-fecha", "-pk")[:200]
    return render(request, "inventario/material_detalle.html", {"material": material, "movimientos": movimientos})


@login_required
@permission_required("inventario.change_material", raise_exception=True)
@require_POST
def material_estado(request, pk):
    material = servicios.cambiar_estado(get_object_or_404(Material, pk=pk))
    estado = "reactivó" if material.activo else "desactivó (ya no admite movimientos)"
    messages.success(request, f"Se {estado} «{material.nombre}».")
    return redirect("inventario:material_detalle", pk=material.pk)


# --------------------------------------------------------------------------- movimientos (SCRUM-26)
@login_required
@permission_required("inventario.view_movimientoinventario", raise_exception=True)
def movimientos(request):
    qs = MovimientoInventario.objects.select_related("material", "responsable").order_by("-fecha", "-pk")
    tipo = request.GET.get("tipo", "")
    if tipo in MovimientoInventario.Tipo.values:
        qs = qs.filter(tipo=tipo)
    material = request.GET.get("material", "")
    if material.isdigit():
        qs = qs.filter(material_id=int(material))
    desde, hasta = parse_date(request.GET.get("desde") or ""), parse_date(request.GET.get("hasta") or "")
    if desde:
        qs = qs.filter(fecha__date__gte=desde)
    if hasta:
        qs = qs.filter(fecha__date__lte=hasta)
    contexto = {
        "movimientos": qs[:300], "tipo": tipo, "material": material, "desde": desde, "hasta": hasta,
        "tipos": MovimientoInventario.Tipo.choices, "lista_materiales": Material.objects.order_by("nombre"),
    }
    plantilla = "inventario/_movimientos_tabla.html" if _es_htmx(request) else "inventario/movimientos.html"
    return render(request, plantilla, contexto)


@login_required
@permission_required("inventario.add_movimientoinventario", raise_exception=True)
def movimiento_crear(request):
    inicial = {"tipo": MovimientoInventario.Tipo.SALIDA}
    if request.GET.get("material", "").isdigit():
        inicial["material"] = int(request.GET["material"])
    if request.GET.get("tipo") in MovimientoInventario.Tipo.values:
        inicial["tipo"] = request.GET["tipo"]
    form = MovimientoForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            movimiento = servicios.registrar_movimiento(d["material"], d["tipo"], d["cantidad"], d["motivo"], request.user)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            m = movimiento.material
            texto = (f"{movimiento.get_tipo_display()} de {movimiento.cantidad.normalize():f} registrada. "
                     f"Stock de «{m.nombre}»: {movimiento.stock_resultante.normalize():f}.")
            if m.activo and m.stock_actual <= m.stock_minimo:
                messages.warning(request, texto + f" Quedó en o bajo el mínimo ({m.stock_minimo.normalize():f}): se avisó a los administradores.")
            else:
                messages.success(request, texto)
            return redirect("inventario:material_detalle", pk=m.pk)
    return render(request, "inventario/movimiento_form.html", {"form": form})
