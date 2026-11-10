from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.acceso import requiere_permiso_directo
from apps.documentos.servicios import guardar_documento
from apps.personal.models import Empleado

from . import breaks as breaks_servicio
from . import servicios
from .forms import DIAS, AsignarHorarioForm, ClasificarNovedadForm, HistorialFiltroForm, HorarioForm, RegistroManualForm
from .models import Descanso, Horario, NovedadAsistencia


def _es_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _fecha(request) -> date:
    valor = parse_date(request.GET.get("fecha") or request.POST.get("fecha") or "")
    hoy = timezone.localdate()
    return min(valor, hoy) if valor else hoy


def _mensaje_error(error: ValidationError) -> str:
    return " ".join(error.messages)


# --------------------------------------------------------------------------- tablero del día (SCRUM-18 / 19)
def _contexto_tablero(fecha):
    filas = servicios.tablero_del_dia(fecha)
    activos = [f for f in filas if f.activo]
    return {
        "fecha": fecha,
        "es_hoy": fecha == timezone.localdate(),
        "anterior": fecha - timedelta(days=1),
        "siguiente": fecha + timedelta(days=1),
        "filas": filas,
        "resumen": {
            "presentes": sum(f.registro is not None for f in activos),
            "atrasos": sum(f.atraso is not None for f in activos),
            "ausentes": sum(f.estado == "Ausente" for f in activos),
            "pendientes": sum(f.estado == "Pendiente" for f in activos),
        },
        "sin_horario": sum(f.horario is None for f in activos),
    }


@login_required
@permission_required("asistencia.view_registroasistencia", raise_exception=True)
def tablero(request):
    contexto = _contexto_tablero(_fecha(request))
    plantilla = "asistencia/_tablero.html" if _es_htmx(request) else "asistencia/tablero.html"
    return render(request, plantilla, contexto)


def _accion_tablero(request, funcion, exito):
    try:
        resultado = funcion()
    except ValidationError as error:
        messages.error(request, _mensaje_error(error))
    else:
        messages.success(request, exito(resultado))
    if _es_htmx(request):
        return render(request, "asistencia/_tablero.html", _contexto_tablero(_fecha(request)))
    return redirect(f"{reverse('asistencia:tablero')}?fecha={_fecha(request).isoformat()}")


@login_required
@permission_required("asistencia.add_registroasistencia", raise_exception=True)
@require_POST
def marcar_entrada(request, pk):
    empleado = get_object_or_404(Empleado, pk=pk)

    def exito(registro):
        texto = f"Entrada de {empleado.nombres} {empleado.apellidos} a las {timezone.localtime(registro.entrada):%H:%M}."
        atraso = registro.novedades.filter(tipo=NovedadAsistencia.Tipo.ATRASO).first()
        return texto + (f" Llegó {atraso.minutos} min tarde." if atraso else "")

    return _accion_tablero(request, lambda: servicios.registrar_entrada(empleado, timezone.now(), request.user), exito)


@login_required
@permission_required("asistencia.change_registroasistencia", raise_exception=True)
@require_POST
def marcar_salida(request, pk):
    empleado = get_object_or_404(Empleado, pk=pk)
    return _accion_tablero(
        request, lambda: servicios.registrar_salida(empleado, timezone.now(), request.user),
        lambda r: f"Salida de {empleado.nombres} {empleado.apellidos} a las {timezone.localtime(r.salida):%H:%M}.",
    )


@login_required
@permission_required("asistencia.add_novedadasistencia", raise_exception=True)
@require_POST
def marcar_ausente(request, pk):
    empleado = get_object_or_404(Empleado, pk=pk)
    fecha = _fecha(request)
    return _accion_tablero(request, lambda: servicios.marcar_ausencia(empleado, fecha),
                           lambda n: f"{empleado.nombres} {empleado.apellidos} quedó ausente el {fecha:%d/%m/%Y}.")


@login_required
@permission_required("asistencia.add_novedadasistencia", raise_exception=True)
@require_POST
def ausencias_del_dia(request):
    fecha = _fecha(request)

    def exito(creadas):
        if creadas == 0:
            return "No hay ausencias nuevas: todos los que debían trabajar tienen entrada o ya estaban ausentes."
        return f"Se registraron {creadas} ausencia{'s' if creadas != 1 else ''} del {fecha:%d/%m/%Y}."

    return _accion_tablero(request, lambda: servicios.marcar_ausencias_del_dia(fecha), exito)


@login_required
@permission_required("asistencia.add_registroasistencia", raise_exception=True)
def registro_manual(request):
    inicial = {}
    if request.method == "GET":
        if request.GET.get("empleado"):
            inicial["empleado"] = request.GET["empleado"]
        if request.GET.get("fecha"):
            inicial["fecha"] = _fecha(request)
    form = RegistroManualForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                servicios.registrar_manual(d["empleado"], d["fecha"], d["hora_entrada"], d["hora_salida"],
                                           request.user, d["observacion"])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, f"Asistencia de {d['empleado'].nombres} {d['empleado'].apellidos} del {d['fecha']:%d/%m/%Y} guardada.")
            return redirect(f"{reverse('asistencia:tablero')}?fecha={d['fecha'].isoformat()}")
    return render(request, "asistencia/registro_manual.html", {"form": form})


# --------------------------------------------------------------------------- novedades (SCRUM-20)
@login_required
@permission_required("asistencia.view_novedadasistencia", raise_exception=True)
def novedades(request):
    estado = request.GET.get("estado", NovedadAsistencia.Estado.PENDIENTE)
    tipo = request.GET.get("tipo", "")
    qs = NovedadAsistencia.objects.select_related("empleado", "soporte", "revisada_por").order_by("-fecha", "empleado__apellidos")
    if estado in NovedadAsistencia.Estado.values:
        qs = qs.filter(estado=estado)
    if tipo in NovedadAsistencia.Tipo.values:
        qs = qs.filter(tipo=tipo)
    contexto = {
        "novedades": qs[:200], "estado": estado, "tipo": tipo,
        "estados": NovedadAsistencia.Estado.choices, "tipos": NovedadAsistencia.Tipo.choices,
        "total_pendientes": NovedadAsistencia.objects.filter(estado=NovedadAsistencia.Estado.PENDIENTE).count(),
    }
    plantilla = "asistencia/_novedades_lista.html" if _es_htmx(request) else "asistencia/novedades.html"
    return render(request, plantilla, contexto)


@login_required
@permission_required("asistencia.change_novedadasistencia", raise_exception=True)
@require_POST
def clasificar_novedad(request, pk):
    novedad = get_object_or_404(NovedadAsistencia.objects.select_related("empleado"), pk=pk)
    form = ClasificarNovedadForm(request.POST, request.FILES, tiene_soporte=novedad.soporte_id is not None)
    if form.is_valid():
        try:
            with transaction.atomic():
                soporte = None
                if form.cleaned_data.get("soporte"):
                    soporte = guardar_documento(
                        form.cleaned_data["soporte"], tipo_codigo="SOPORTE_ASISTENCIA", empleado=novedad.empleado,
                        usuario=request.user, descripcion=f"{novedad.get_tipo_display()} del {novedad.fecha:%d/%m/%Y}",
                    )
                servicios.clasificar(novedad, form.cleaned_data["estado"], form.cleaned_data["motivo"], request.user, soporte)
        except (ValidationError, IntegrityError) as error:
            texto = _mensaje_error(error) if isinstance(error, ValidationError) else "No se pudo guardar el soporte."
            form.add_error(None, texto)
        else:
            if _es_htmx(request):
                return render(request, "asistencia/_novedad.html", {"n": novedad, "recien": True})
            messages.success(request, "Novedad clasificada.")
            return redirect("asistencia:novedades")
    if _es_htmx(request):
        return render(request, "asistencia/_novedad.html", {"n": novedad, "form": form})
    messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect("asistencia:novedades")


@login_required
@permission_required("asistencia.change_novedadasistencia", raise_exception=True)
@require_POST
def reabrir_novedad(request, pk):
    novedad = servicios.reabrir(get_object_or_404(NovedadAsistencia, pk=pk))
    if _es_htmx(request):
        return render(request, "asistencia/_novedad.html", {"n": novedad})
    return redirect("asistencia:novedades")


# --------------------------------------------------------------------------- historial (SCRUM-21)
@login_required
@permission_required("asistencia.view_registroasistencia", raise_exception=True)
def historial(request):
    filtro = HistorialFiltroForm(request.GET or None)
    desde, hasta = filtro.rango()
    empleado = filtro.cleaned_data.get("empleado") if filtro.is_valid() else None
    filas, resumen = servicios.historial(desde, hasta, empleado)
    contexto = {"filtro": filtro, "filas": filas, "resumen": resumen, "desde": desde, "hasta": hasta, "empleado": empleado}
    plantilla = "asistencia/_historial_resultado.html" if _es_htmx(request) else "asistencia/historial.html"
    return render(request, plantilla, contexto)


# --------------------------------------------------------------------------- horarios
@login_required
@permission_required("asistencia.view_horario", raise_exception=True)
def horarios(request):
    form_horario = HorarioForm(prefix="h")
    form_asignar = AsignarHorarioForm(prefix="a")
    if request.method == "POST":
        if "crear_horario" in request.POST and request.user.has_perm("asistencia.add_horario"):
            form_horario = HorarioForm(request.POST, prefix="h")
            if form_horario.is_valid():
                horario = form_horario.save()
                messages.success(request, f"Horario «{horario.nombre}» creado.")
                return redirect("asistencia:horarios")
        elif "asignar" in request.POST and request.user.has_perm("asistencia.add_asignacionhorario"):
            form_asignar = AsignarHorarioForm(request.POST, prefix="a")
            if form_asignar.is_valid():
                d = form_asignar.cleaned_data
                try:
                    with transaction.atomic():
                        total = servicios.asignar_horario(d["horario"], list(d["empleados"]), d["desde"])
                except (ValidationError, IntegrityError) as error:
                    form_asignar.add_error(None, _mensaje_error(error) if isinstance(error, ValidationError)
                                           else "Ese empleado ya tiene un horario que se cruza con esas fechas.")
                else:
                    messages.success(request, f"Horario «{d['horario'].nombre}» asignado a {total} empleado{'s' if total != 1 else ''} desde el {d['desde']:%d/%m/%Y}.")
                    return redirect("asistencia:horarios")
        else:
            messages.error(request, "No tiene permiso para esta acción.")

    hoy = timezone.localdate()
    vigentes = {a.empleado_id: a for a in servicios.asignaciones_vigentes(hoy)}
    empleados = Empleado.objects.exclude(estado=Empleado.Estado.DESVINCULADO).order_by("apellidos", "nombres")
    return render(request, "asistencia/horarios.html", {
        "horarios": Horario.objects.order_by("-activo", "nombre"),
        "asignaciones": [(e, vigentes.get(e.pk)) for e in empleados],
        "form_horario": form_horario,
        "form_asignar": form_asignar,
        "dias": dict(DIAS),
    })


# --------------------------------------------------------------------------- finalizar día (empleado)
@login_required
@require_POST
def finalizar_dia(request):
    """
    Botón «Finalizar día» de la barra superior: registra la salida con la hora exacta del servidor
    y cierra la sesión. Si alguien no lo pulsa, revisar_salidas crea la incidencia y avisa al administrador.
    """
    from django.contrib.auth import logout

    empleado = getattr(request.user, "empleado", None)
    try:
        registro = servicios.finalizar_dia(empleado, usuario=request.user)
    except ValidationError as error:
        messages.error(request, _mensaje_error(error))
        siguiente = request.POST.get("siguiente", "")
        if url_has_allowed_host_and_scheme(siguiente, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(siguiente)
        return redirect("inicio")
    salida = timezone.localtime(registro.salida)
    anticipada = registro.novedades.filter(tipo=NovedadAsistencia.Tipo.SALIDA_ANTICIPADA).first()
    logout(request)
    texto = f"Día finalizado. Salida registrada a las {salida:%H:%M:%S}."
    if anticipada:
        texto += f" Quedó como salida anticipada ({anticipada.minutos} min antes de la hora de salida)."
    messages.success(request, texto)
    return redirect("login")


# --------------------------------------------------------------------------- almuerzo (empleado)
COOKIE_ALMUERZO = "callflow_almuerzo"
SAL_COOKIE_ALMUERZO = "asistencia.almuerzo"


@login_required
@require_POST
def salir_almuerzo(request):
    """
    Botón «Salir a almorzar»: marca la hora, cierra la sesión y deja una cookie firmada para que la
    pantalla de acceso muestre el contador. El regreso se registra en el siguiente inicio de sesión.
    """
    from django.contrib.auth import logout

    try:
        registro = servicios.iniciar_almuerzo(getattr(request.user, "empleado", None), usuario=request.user)
    except ValidationError as error:
        messages.error(request, _mensaje_error(error))
        return redirect("inicio")
    regreso = timezone.localtime(servicios.regreso_esperado(registro))
    logout(request)
    messages.info(request, f"Buen provecho. Salida al almuerzo a las {timezone.localtime(registro.almuerzo_inicio):%H:%M}; "
                           f"debe volver a las {regreso:%H:%M}. Al regresar, inicie sesión para registrar su vuelta.")
    respuesta = redirect("login")
    respuesta.set_signed_cookie(COOKIE_ALMUERZO, str(registro.pk), salt=SAL_COOKIE_ALMUERZO, max_age=12 * 3600,
                                httponly=True, samesite="Lax", secure=request.is_secure())
    return respuesta

# --------------------------------------------------------------------------- breaks
requiere_breaks = requiere_permiso_directo(breaks_servicio.PERMISO, "Dar breaks es exclusivo del coordinador.")
DURACIONES_RAPIDAS = (10, 15, 20, 30)


def _panel_breaks(request) -> dict:
    ahora = timezone.now()
    actual = breaks_servicio.en_curso(ahora)
    hace_una_semana = ahora - timedelta(days=7)
    recientes = [breaks_servicio.detalle(d, ahora) for d in
                 Descanso.objects.filter(inicio__gte=hace_una_semana).exclude(pk=getattr(actual, "pk", None))
                 .select_related("enviado_por").order_by("-inicio")[:15]]
    return {"actual": breaks_servicio.detalle(actual, ahora) if actual else None, "recientes": recientes,
            "ahora": ahora, "tolerancia": breaks_servicio.tolerancia()}


@requiere_breaks
def breaks(request):
    """El coordinador da un break: el tiempo corre desde que lo envía y les llega a los vendedores."""
    if request.method == "POST":
        try:
            minutos = int(request.POST.get("minutos", ""))
        except ValueError:
            messages.error(request, "Indique cuántos minutos dura el break.")
        else:
            try:
                descanso = breaks_servicio.dar_break(request.user, minutos, request.POST.get("mensaje", ""))
            except ValidationError as error:
                messages.error(request, _mensaje_error(error))
            else:
                n = descanso.regresos.count()
                messages.success(request, f"Break de {minutos} min enviado a {n} vendedor{'es' if n != 1 else ''}: "
                                          f"vuelven a las {timezone.localtime(descanso.fin):%H:%M}.")
        return redirect("asistencia:breaks")
    contexto = _panel_breaks(request) | {"participantes": breaks_servicio.participantes(), "rapidas": DURACIONES_RAPIDAS}
    return render(request, "asistencia/breaks.html", contexto)


@requiere_breaks
def breaks_panel(request):
    """HTMX cada 5 s: cómo va el break en curso y los recientes."""
    return render(request, "asistencia/_breaks_panel.html", _panel_breaks(request))


@login_required
@require_POST
def volvi_del_break(request):
    """El vendedor marca que volvió del break."""
    empleado = getattr(request.user, "empleado", None)
    try:
        regreso = breaks_servicio.volver_de_break(empleado)
    except ValidationError as error:
        messages.error(request, _mensaje_error(error))
    else:
        if regreso.minutos_tarde:
            messages.warning(request, f"Regreso registrado: volviste {regreso.minutos_tarde} min tarde del break; "
                                      "se informó al coordinador.")
        else:
            messages.success(request, "Regreso del break registrado a tiempo. ¡Buen trabajo!")
    # Vuelve a la página donde estaba (el aviso aparece en cualquier página).
    anterior = request.headers.get("Referer", "")
    if anterior and url_has_allowed_host_and_scheme(anterior, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(anterior)
    return redirect("inicio")
