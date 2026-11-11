from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.core.models import Usuario

from . import servicios
from .acceso import requiere_ti
from .forms import EquipoForm, EventoForm
from .models import Equipo, SesionEquipo


@requiere_ti
def equipos(request):
    qs = Equipo.objects.select_related("asignado_a").annotate(n_eventos=Count("eventos")).order_by("estado", "codigo")
    texto = request.GET.get("q", "").strip()
    if texto:
        qs = qs.filter(Q(codigo__icontains=texto) | Q(marca__icontains=texto) | Q(modelo__icontains=texto)
                       | Q(numero_serie__icontains=texto) | Q(hostname__icontains=texto) | Q(direccion_ip__icontains=texto)
                       | Q(ubicacion__icontains=texto) | Q(asignado_a__nombres__icontains=texto)
                       | Q(asignado_a__apellidos__icontains=texto))
    tipo = request.GET.get("tipo", "")
    if tipo in Equipo.Tipo.values:
        qs = qs.filter(tipo=tipo)
    estado = request.GET.get("estado", "")
    if estado in Equipo.Estado.values:
        qs = qs.filter(estado=estado)
    lista = list(qs)
    en_linea = {s.ip for s in servicios.conectados()}
    for e in lista:
        e.en_linea = bool(e.direccion_ip and e.direccion_ip in en_linea)
    return render(request, "ti/equipos.html", {
        "equipos": lista, "q": texto, "tipo": tipo, "estado": estado, "tipos": Equipo.Tipo.choices,
        "estados": Equipo.Estado.choices, "resumen": servicios.resumen(), "hoy": timezone.localdate(),
    })


@requiere_ti
def equipo_crear(request):
    inicial = {}
    if request.GET.get("ip"):  # «Registrar este equipo» desde Conectados
        inicial = {"direccion_ip": request.GET["ip"], "tipo": Equipo.Tipo.ESCRITORIO}
    form = EquipoForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        equipo = servicios.guardar_equipo(form.save(commit=False), usuario=request.user)
        messages.success(request, f"Equipo {equipo.codigo} registrado.")
        return redirect("ti:detalle", pk=equipo.pk)
    return render(request, "ti/equipo_form.html", {"form": form, "nuevo": True})


@requiere_ti
def equipo_editar(request, pk):
    equipo = get_object_or_404(Equipo, pk=pk)
    anterior = {"asignado_a": equipo.asignado_a, "estado": equipo.estado}
    form = EquipoForm(request.POST or None, instance=equipo)
    if request.method == "POST" and form.is_valid():
        servicios.guardar_equipo(form.save(commit=False), usuario=request.user, anterior=anterior)
        messages.success(request, f"Equipo {equipo.codigo} actualizado.")
        return redirect("ti:detalle", pk=equipo.pk)
    return render(request, "ti/equipo_form.html", {"form": form, "equipo": equipo})


@requiere_ti
def equipo_detalle(request, pk):
    equipo = get_object_or_404(Equipo.objects.select_related("asignado_a", "registrado_por"), pk=pk)
    conexiones = (SesionEquipo.objects.filter(ip=equipo.direccion_ip).select_related("usuario")[:15]
                  if equipo.direccion_ip else [])
    return render(request, "ti/equipo_detalle.html", {
        "equipo": equipo, "eventos": equipo.eventos.select_related("empleado", "registrado_por"),
        "conexiones": conexiones, "hoy": timezone.localdate(),
    })


@requiere_ti
def evento_crear(request, pk):
    equipo = get_object_or_404(Equipo, pk=pk)
    form = EventoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        evento = form.save(commit=False)
        evento.equipo, evento.registrado_por = equipo, request.user
        evento.save()
        messages.success(request, f"{evento.get_tipo_display()} registrado en la hoja de vida de {equipo.codigo}.")
        return redirect("ti:detalle", pk=equipo.pk)
    return render(request, "ti/evento_form.html", {"form": form, "equipo": equipo})


@requiere_ti
def conectados(request):
    sesiones = list(servicios.conectados())
    por_ip = servicios.equipos_por_ip(s.ip for s in sesiones)
    for s in sesiones:
        s.equipo = por_ip.get(s.ip)

    # Historial de conexiones (filtrable)
    hoy = timezone.localdate()
    desde = parse_date(request.GET.get("desde", "")) or hoy - timedelta(days=6)
    hasta = parse_date(request.GET.get("hasta", "")) or hoy
    if hasta < desde:
        desde, hasta = hasta, desde
    historial = SesionEquipo.objects.filter(inicio__date__gte=desde, inicio__date__lte=hasta).select_related("usuario", "cerrada_por")
    usuario = request.GET.get("usuario", "")
    if usuario.isdigit():
        historial = historial.filter(usuario_id=int(usuario))
    pagina = Paginator(historial.order_by("-inicio"), 50).get_page(request.GET.get("pagina"))
    por_ip_hist = servicios.equipos_por_ip(s.ip for s in pagina)
    for s in pagina:
        s.equipo = por_ip_hist.get(s.ip)
    return render(request, "ti/conectados.html", {
        "sesiones": sesiones, "pagina": pagina, "desde": desde, "hasta": hasta, "usuario": usuario,
        "usuarios": Usuario.objects.filter(sesiones_equipo__isnull=False).distinct().order_by("first_name", "last_name"),
        "minutos": servicios.MINUTOS_CONECTADO, "sin_registrar": sum(1 for s in sesiones if s.equipo is None),
        "ahora": timezone.localtime(),
    })


@requiere_ti
@require_POST
def cerrar_sesion(request, pk):
    sesion = get_object_or_404(SesionEquipo.objects.select_related("usuario"), pk=pk, fin__isnull=True)
    if sesion.clave_sesion == request.session.session_key:
        messages.error(request, "Esa es su propia sesión: para salir use «Cerrar sesión».")
    else:
        servicios.cerrar_sesion_remota(sesion, request.user)
        messages.success(request, f"Se cerró la sesión de {sesion.usuario.get_full_name() or sesion.usuario.username} "
                                  f"({sesion.ip or 'IP desconocida'}). Tendrá que volver a ingresar en ese equipo.")
    return redirect("ti:conectados")
