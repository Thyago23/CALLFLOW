from datetime import timedelta
from pathlib import Path

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import format_html
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from apps.core.models import Usuario

from . import actas_pdf, servicios
from .acceso import requiere_ti
from .forms import ActaFirmadaForm, DevolucionForm, EntregaForm, EquipoForm, EventoForm
from .models import ActaEquipo, Equipo, EventoEquipo, SesionEquipo


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
        "equipo": equipo, "eventos": equipo.eventos.select_related("empleado", "registrado_por", "acta"),
        "conexiones": conexiones, "hoy": timezone.localdate(), "entrega": servicios.entrega_abierta(equipo),
        "actas": equipo.actas.select_related("empleado", "registrado_por"), "form_firmada": ActaFirmadaForm(),
        "planes": equipo.planes.order_by("proxima_fecha"), "licencias": equipo.licencias.filter(activa=True),
        "tickets": equipo.tickets.order_by("-creado_en")[:10],
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


# --------------------------------------------------------------------------- préstamos y actas de entrega
def _acta_emitida(request, acta: ActaEquipo):
    messages.success(request, format_html(
        '{} registrada con el acta {}. <a href="{}" target="_blank" class="alert-link">Descargar el acta para firmar</a>.',
        acta.get_tipo_display(), acta.numero, reverse("ti:acta_pdf", args=[acta.pk])))
    return redirect(reverse("ti:detalle", args=[acta.equipo_id]) + "#actas")


@requiere_ti
def entregar(request, pk):
    equipo = get_object_or_404(Equipo.objects.select_related("asignado_a"), pk=pk)
    if equipo.asignado_a_id:
        messages.error(request, f"El equipo {equipo.codigo} ya está entregado a {equipo.asignado_a}: registre primero la devolución.")
        return redirect("ti:detalle", pk=pk)
    if equipo.estado in (Equipo.Estado.BAJA, Equipo.Estado.REPARACION):
        messages.error(request, f"El equipo {equipo.codigo} está {equipo.get_estado_display().lower()}: no se puede entregar.")
        return redirect("ti:detalle", pk=pk)
    form = EntregaForm(request.POST or None, equipo=equipo)
    if request.method == "POST" and form.is_valid():
        try:
            acta = servicios.entregar_equipo(equipo, **form.cleaned_data, usuario=request.user)
        except servicios.OperacionNoPermitida as error:
            messages.error(request, str(error))
            return redirect("ti:detalle", pk=pk)
        return _acta_emitida(request, acta)
    return render(request, "ti/acta_form.html", {"form": form, "equipo": equipo, "es_entrega": True})


@requiere_ti
def devolver(request, pk):
    equipo = get_object_or_404(Equipo.objects.select_related("asignado_a"), pk=pk)
    if not equipo.asignado_a_id:
        messages.error(request, f"El equipo {equipo.codigo} no está entregado a nadie.")
        return redirect("ti:detalle", pk=pk)
    abierta = servicios.entrega_abierta(equipo)
    form = DevolucionForm(request.POST or None, equipo=equipo, entrega=abierta)
    if request.method == "POST" and form.is_valid():
        try:
            acta = servicios.devolver_equipo(equipo, **form.cleaned_data, usuario=request.user)
        except servicios.OperacionNoPermitida as error:
            messages.error(request, str(error))
            return redirect("ti:detalle", pk=pk)
        return _acta_emitida(request, acta)
    return render(request, "ti/acta_form.html", {"form": form, "equipo": equipo, "es_entrega": False, "abierta": abierta})


@requiere_ti
def acta_pdf(request, pk):
    acta = get_object_or_404(ActaEquipo.objects.select_related(
        "equipo", "empleado", "empleado__cargo", "registrado_por", "entrega_acta"), pk=pk)
    respuesta = HttpResponse(actas_pdf.generar_pdf_acta(acta), content_type="application/pdf")
    respuesta["Content-Disposition"] = content_disposition_header(False, actas_pdf.nombre_archivo(acta))
    return respuesta


@requiere_ti
@require_POST
def acta_firmada_subir(request, pk):
    acta = get_object_or_404(ActaEquipo, pk=pk)
    form = ActaFirmadaForm(request.POST, request.FILES)
    if form.is_valid():
        reemplazo = bool(acta.firmada)
        servicios.adjuntar_acta_firmada(acta, form.cleaned_data["archivo"], mime=form.mime, usuario=request.user)
        messages.success(request, f"Acta firmada {acta.numero} {'reemplazada' if reemplazo else 'guardada'}.")
    else:
        messages.error(request, " ".join(form.errors.get("archivo", ["No se pudo guardar el archivo."])))
    if request.POST.get("volver") == "prestamos":
        return redirect("ti:prestamos")
    return redirect(reverse("ti:detalle", args=[acta.equipo_id]) + "#actas")


@requiere_ti
def acta_firmada_ver(request, pk):
    """El acta firmada solo la ve TI: el archivo no es público."""
    acta = get_object_or_404(ActaEquipo.objects.select_related("equipo", "empleado"), pk=pk)
    if not acta.firmada:
        raise Http404("Esta acta todavía no tiene la copia firmada.")
    extension = acta.firmada.name.rsplit(".", 1)[-1]
    respuesta = FileResponse(acta.firmada.open("rb"), content_type=acta.firmada_mime or "application/octet-stream")
    respuesta["Content-Disposition"] = content_disposition_header(
        False, f"{acta.numero} firmada {acta.equipo.codigo} {acta.empleado.apellidos}.{extension}")
    return respuesta


@requiere_ti
def prestamos(request):
    hoy = timezone.localdate()
    abiertos = list(servicios.prestamos_abiertos())
    for a in abiertos:
        a.vencido = bool(a.es_prestamo and a.devolucion_prevista < hoy)
        a.por_vencer = bool(a.es_prestamo and not a.vencido and a.devolucion_prevista <= hoy + timedelta(days=3))
    actas = ActaEquipo.objects.select_related("equipo", "empleado", "registrado_por")
    firma = request.GET.get("firma", "")
    if firma == "pendiente":
        actas = actas.filter(firmada="")
    elif firma == "firmada":
        actas = actas.exclude(firmada="")
    pagina = Paginator(actas.order_by("-fecha", "-pk"), 30).get_page(request.GET.get("pagina"))
    return render(request, "ti/prestamos.html", {
        "abiertos": abiertos, "pagina": pagina, "firma": firma, "hoy": hoy,
        "n_prestamos": sum(1 for a in abiertos if a.es_prestamo), "n_vencidos": sum(1 for a in abiertos if a.vencido),
        "n_sin_firma": ActaEquipo.objects.filter(firmada="").count(),
    })


# --------------------------------------------------------------------------- estado del servidor
@requiere_ti
def servidor(request):
    from django.conf import settings
    from django.db import DatabaseError

    from . import registro_errores
    from . import servidor as estado

    dias = int(request.GET["dias"]) if request.GET.get("dias") in ("1", "7", "30") else 7
    try:
        base = estado.base_de_datos()
    except DatabaseError as error:  # la página debe mostrarse aunque la base responda mal
        base = {"error": str(error).strip().splitlines()[0] if str(error).strip() else error.__class__.__name__}
    archivos, n_archivos = estado.tamano_carpeta(Path(settings.MEDIA_ROOT))
    errores = registro_errores.errores_recientes(dias)
    seguridad = registro_errores.eventos_seguridad(dias)
    return render(request, "ti/servidor.html", {
        "discos": estado.discos(), "base": base, "respaldo": estado.ultimo_respaldo(), "sistema": estado.sistema(),
        "archivos": archivos, "n_archivos": n_archivos, "errores": errores[:100], "n_grupos": len(errores),
        "n_ocurrencias": sum(e["veces"] for e in errores), "errores_24h": registro_errores.contar_desde(24),
        "seguridad": seguridad[:100], "n_seguridad": len(seguridad), "seguridad_24h": registro_errores.contar_seguridad_desde(24),
        "dias": dias, "ahora": timezone.localtime(), "libre_critico": estado.LIBRE_CRITICO, "libre_bajo": estado.LIBRE_BAJO,
    })


# --------------------------------------------------------------------------- licencias y servicios
@requiere_ti
def licencias(request):
    from .models import Licencia

    qs = Licencia.objects.prefetch_related("equipos").order_by(F("fecha_vencimiento").asc(nulls_last=True), "nombre")
    tipo = request.GET.get("tipo", "")
    if tipo in Licencia.Tipo.values:
        qs = qs.filter(tipo=tipo)
    ver = request.GET.get("ver", "")
    lista = list(qs.filter(activa=True) if ver != "todas" else qs)
    for lic in lista:
        lic.estado_actual = lic.situacion if lic.activa else "inactiva"
    return render(request, "ti/licencias.html", {
        "licencias": lista, "tipo": tipo, "ver": ver, "tipos": Licencia.Tipo.choices,
        "n_vencidas": sum(1 for x in lista if x.estado_actual == "vencida"),
        "n_por_vencer": sum(1 for x in lista if x.estado_actual == "por_vencer"),
        "costo_anual": sum((x.costo or 0) for x in lista if x.activa),
    })


def _licencia_form(request, licencia=None):
    from .forms import LicenciaForm

    form = LicenciaForm(request.POST or None, instance=licencia)
    if request.method == "POST" and form.is_valid():
        nueva = licencia is None
        licencia = form.save(commit=False)
        if nueva:
            licencia.registrado_por = request.user
        licencia.save()
        form.save_m2m()
        messages.success(request, f"{licencia} {'registrada' if nueva else 'actualizada'}.")
        return redirect("ti:licencias")
    return render(request, "ti/licencia_form.html", {"form": form, "licencia": licencia})


@requiere_ti
def licencia_crear(request):
    return _licencia_form(request)


@requiere_ti
def licencia_editar(request, pk):
    from .models import Licencia

    return _licencia_form(request, get_object_or_404(Licencia, pk=pk))


# --------------------------------------------------------------------------- mantenimiento preventivo
@requiere_ti
def mantenimiento(request):
    from .models import PlanMantenimiento

    qs = PlanMantenimiento.objects.select_related("equipo", "equipo__asignado_a").exclude(equipo__estado=Equipo.Estado.BAJA)
    texto = request.GET.get("q", "").strip()
    if texto:
        qs = qs.filter(Q(equipo__codigo__icontains=texto) | Q(actividad__icontains=texto) | Q(equipo__ubicacion__icontains=texto))
    ver = request.GET.get("ver", "")
    planes = list(qs.filter(activo=True) if ver != "todos" else qs)
    situacion = request.GET.get("situacion", "")
    if situacion in ("vencido", "por_vencer", "al_dia"):
        planes = [p for p in planes if p.situacion == situacion]
    from . import mantenimiento as reglas

    hoy = timezone.localdate()
    return render(request, "ti/mantenimiento.html", {
        "planes": planes, "q": texto, "ver": ver, "situacion": situacion, "resumen": reglas.resumen(), "hoy": hoy,
        "hechos": EventoEquipo.objects.filter(tipo=EventoEquipo.Tipo.MANTENIMIENTO, fecha__gte=hoy - timedelta(days=90))
                                      .select_related("equipo").order_by("-fecha", "-pk")[:15],
        "sin_plan": Equipo.objects.filter(tipo__in=[Equipo.Tipo.ESCRITORIO, Equipo.Tipo.LAPTOP, Equipo.Tipo.SERVIDOR])
                                  .exclude(estado=Equipo.Estado.BAJA).filter(planes__isnull=True).count(),
    })


@requiere_ti
def plan_crear(request):
    from . import mantenimiento as reglas
    from .forms import PlanNuevoForm

    inicial = {"primera_fecha": timezone.localdate()}
    if request.GET.get("equipo", "").isdigit():
        inicial["equipo"] = request.GET["equipo"]
    form = PlanNuevoForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        creados, omitidos = reglas.crear_planes(
            form.equipos(), actividad=datos["actividad"], cada_meses=datos["cada_meses"], primera_fecha=datos["primera_fecha"],
            dias_aviso=datos["dias_aviso"], observacion=datos["observacion"], usuario=request.user)
        if creados:
            messages.success(request, f"Plan «{datos['actividad']}» creado en {len(creados)} equipo{'s' if len(creados) != 1 else ''}.")
        if omitidos:
            messages.warning(request, "Ya tenían esa actividad: " + ", ".join(e.codigo for e in omitidos[:15])
                             + ("…" if len(omitidos) > 15 else ""))
        if not creados and not omitidos:
            messages.warning(request, "No hay equipos de ese tipo.")
        if len(creados) == 1 and datos.get("equipo"):
            return redirect("ti:detalle", pk=datos["equipo"].pk)
        return redirect("ti:mantenimiento")
    return render(request, "ti/plan_form.html", {"form": form, "nuevo": True})


@requiere_ti
def plan_editar(request, pk):
    from .forms import PlanForm
    from .models import PlanMantenimiento

    plan = get_object_or_404(PlanMantenimiento.objects.select_related("equipo"), pk=pk)
    form = PlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Plan de {plan.equipo.codigo} actualizado.")
        return redirect("ti:mantenimiento")
    return render(request, "ti/plan_form.html", {"form": form, "plan": plan})


@requiere_ti
def plan_realizado(request, pk):
    from . import mantenimiento as reglas
    from .forms import MantenimientoRealizadoForm
    from .models import PlanMantenimiento

    plan = get_object_or_404(PlanMantenimiento.objects.select_related("equipo"), pk=pk)
    form = MantenimientoRealizadoForm(request.POST or None, initial={"fecha": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            reglas.registrar_realizado(plan, **form.cleaned_data, usuario=request.user)
        except servicios.OperacionNoPermitida as error:
            messages.error(request, str(error))
        else:
            plan.refresh_from_db()
            messages.success(request, f"Mantenimiento de {plan.equipo.codigo} registrado en su hoja de vida. "
                                      f"Próximo: {plan.proxima_fecha:%d/%m/%Y}.")
            return redirect(request.POST.get("volver") == "equipo" and reverse("ti:detalle", args=[plan.equipo_id])
                            or reverse("ti:mantenimiento"))
    return render(request, "ti/plan_realizado.html", {"form": form, "plan": plan, "volver": request.GET.get("volver", "")})


# --------------------------------------------------------------------------- accesos y bloqueos
@requiere_ti
def accesos(request):
    from . import ingresos

    dias = int(request.GET["dias"]) if request.GET.get("dias") in ("1", "7", "30") else 7
    return render(request, "ti/accesos.html", {
        "bloqueadas": ingresos.cuentas_bloqueadas(), "intentos": ingresos.intentos_fallidos(dias), "dias": dias,
        "config": ingresos.configuracion(), "ahora": timezone.localtime(),
    })


@requiere_ti
@require_POST
def desbloquear(request):
    from . import ingresos

    usuario = request.POST.get("usuario", "").strip()
    if usuario:
        ingresos.desbloquear(usuario, request.user, request)
        messages.success(request, f"Se desbloqueó la cuenta {usuario}. Ya puede volver a intentar ingresar.")
    return redirect("ti:accesos")


# --------------------------------------------------------------------------- respaldos
@requiere_ti
def respaldos(request):
    from . import respaldos as historial

    dias = int(request.GET["dias"]) if request.GET.get("dias") in ("7", "30", "90") else 30
    return render(request, "ti/respaldos.html", {
        "estado": historial.estado(dias), "copias": historial.copias_en_carpeta(), "dias": dias, "ahora": timezone.localtime(),
    })
