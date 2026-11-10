from calendar import monthrange

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from . import servicios
from .acceso import requiere_acceso
from .forms import ChipForm, ReposicionForm
from .models import Chip, Operadora


def _mes_actual():
    hoy = timezone.localdate()
    return hoy.replace(day=1), hoy.replace(day=monthrange(hoy.year, hoy.month)[1])


@requiere_acceso
def chips(request):
    qs = (Chip.objects.select_related("asignado_a").annotate(n_reposiciones=Count("reposiciones"))
          .order_by("estado", "numero"))
    texto = request.GET.get("q", "").strip()
    if texto:
        qs = qs.filter(Q(numero__icontains=texto) | Q(iccid__icontains=texto)
                       | Q(asignado_a__nombres__icontains=texto) | Q(asignado_a__apellidos__icontains=texto)
                       | Q(reposiciones__numero_anterior__icontains=texto)).distinct()
    operadora = request.GET.get("operadora", "")
    if operadora in Operadora.values:
        qs = qs.filter(operadora=operadora)
    estado = request.GET.get("estado", "")
    if estado in Chip.Estado.values:
        qs = qs.filter(estado=estado)
    desde, hasta = _mes_actual()
    return render(request, "chips/chips.html", {
        "chips": qs, "q": texto, "operadora": operadora, "estado": estado,
        "operadoras": Operadora.choices, "estados": Chip.Estado.choices, "resumen": servicios.resumen(desde, hasta),
    })


@requiere_acceso
def chip_crear(request):
    form = ChipForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        chip = form.save(commit=False)
        chip.registrado_por = request.user
        chip.save()
        messages.success(request, f"Chip {chip.numero} registrado.")
        return redirect("chips:detalle", pk=chip.pk)
    return render(request, "chips/chip_form.html", {"form": form, "nuevo": True})


@requiere_acceso
def chip_editar(request, pk):
    chip = get_object_or_404(Chip, pk=pk)
    form = ChipForm(request.POST or None, instance=chip)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Chip {chip.numero} actualizado.")
        return redirect("chips:detalle", pk=chip.pk)
    return render(request, "chips/chip_form.html", {"form": form, "chip": chip})


@requiere_acceso
def chip_detalle(request, pk):
    chip = get_object_or_404(Chip.objects.select_related("asignado_a", "registrado_por"), pk=pk)
    return render(request, "chips/chip_detalle.html", {
        "chip": chip, "reposiciones": chip.reposiciones.select_related("registrado_por").order_by("-fecha", "-pk"),
    })


@requiere_acceso
def reposicion_crear(request, pk):
    chip = get_object_or_404(Chip, pk=pk)
    if chip.estado == Chip.Estado.BAJA:
        messages.error(request, "El chip está dado de baja: no se le pueden registrar reposiciones.")
        return redirect("chips:detalle", pk=chip.pk)
    form = ReposicionForm(request.POST or None, chip=chip)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        anterior = chip.numero
        try:
            reposicion = servicios.registrar_reposicion(
                chip, fecha=d["fecha"], motivo=d["motivo"], numero_nuevo=d["numero_nuevo"], iccid_nuevo=d["iccid_nuevo"],
                operadora=d["operadora"], costo=d["costo"], observacion=d["observacion"], usuario=request.user)
        except ValidationError as error:
            if hasattr(error, "error_dict"):
                for campo, errores in error.error_dict.items():
                    form.add_error(campo if campo in form.fields else None, errores)
            else:
                form.add_error(None, error)
        else:
            if reposicion.cambio_numero:
                messages.success(request, f"Reposición registrada. El número cambió de {anterior} a {reposicion.numero_nuevo}.")
            else:
                messages.success(request, f"Reposición del chip {anterior} registrada (mantiene el mismo número).")
            return redirect("chips:detalle", pk=chip.pk)
    return render(request, "chips/reposicion_form.html", {"form": form, "chip": chip})


@requiere_acceso
def reporte(request):
    desde_mes, hasta_mes = _mes_actual()
    desde = parse_date(request.GET.get("desde", "")) or desde_mes
    hasta = parse_date(request.GET.get("hasta", "")) or hasta_mes
    if hasta < desde:
        desde, hasta = hasta, desde
    operadora = request.GET.get("operadora", "")
    if operadora not in Operadora.values:
        operadora = ""
    rango = (f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}" if desde != hasta else f"{desde:%d/%m/%Y}")
    if operadora:
        rango += f" · {Operadora(operadora).label}"
    return render(request, "chips/reporte.html", {
        "reporte": servicios.reporte_reposiciones(desde, hasta, operadora), "desde": desde, "hasta": hasta,
        "operadora": operadora, "operadoras": Operadora.choices, "rango_texto": rango, "generado_en": timezone.localtime(),
    })
