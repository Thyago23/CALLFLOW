import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.core import validadores
from apps.personal.models import Empleado
from apps.ventas.models import FormularioDAMI
from apps.ventas.servicios import empleado_de

from . import servicios
from .forms import CorreccionForm, NotaPaseForm, ReasignarForm, ResponsablesForm, ResultadoForm, inicial_motivo
from .models import GestionCierre

Estado = FormularioDAMI.Estado
VER = "cierre.view_gestioncierre"
GESTIONAR = "cierre.add_gestioncierre"


def _es_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _mes_de(request) -> tuple[int, int]:
    hoy = timezone.localdate()
    try:
        anio, mes = (int(x) for x in request.GET.get("mes", "").split("-"))
        date(anio, mes, 1)
    except ValueError:
        return hoy.year, hoy.month
    return anio, mes


def _navegacion_mes(anio: int, mes: int) -> dict:
    anterior = (anio - 1, 12) if mes == 1 else (anio, mes - 1)
    siguiente = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return {"mes_actual": date(anio, mes, 1), "mes_param": f"{anio}-{mes:02d}",
            "anterior": f"{anterior[0]}-{anterior[1]:02d}", "siguiente": f"{siguiente[0]}-{siguiente[1]:02d}"}


# --------------------------------------------------------------------------- bandeja (SCRUM-48, 49)
@login_required
@permission_required(VER, raise_exception=True)
def bandeja(request):
    vista = request.GET.get("vista", "cola")
    qs = servicios.gestionados() if vista == "gestionados" else servicios.bandeja()

    texto = request.GET.get("q", "").strip()
    if texto:
        ident = validadores.normalizar_identificacion(texto)
        filtro = (Q(cliente__identificacion__icontains=ident) | Q(cliente__ruc__icontains=ident)
                  | Q(cliente__nombres__icontains=texto) | Q(cliente__apellidos__icontains=texto))
        if texto.upper().startswith("DAMI-") and texto[5:].isdigit():
            filtro |= Q(pk=int(texto[5:]))
        qs = qs.filter(filtro)
    estado = request.GET.get("estado", "")
    if vista == "gestionados" and estado in Estado.values:
        qs = qs.filter(estado=estado)
    elif vista != "gestionados" and estado in FormularioDAMI.Etapa.values:
        qs = qs.filter(etapa=estado)
    vendedor = request.GET.get("vendedor", "")
    if vendedor.isdigit():
        qs = qs.filter(vendedor_id=int(vendedor))
    desde, hasta = parse_date(request.GET.get("desde") or ""), parse_date(request.GET.get("hasta") or "")
    if desde:
        qs = qs.filter(enviado_en__date__gte=desde)
    if hasta:
        qs = qs.filter(enviado_en__date__lte=hasta)
    solo_prioritarios = request.GET.get("prioritarios") == "1"
    if solo_prioritarios:
        qs = qs.filter(prioridad=1)
    empleado = empleado_de(request.user)
    # Los supervisores ven primero lo que les toca; pueden quitar el filtro para ver toda la cola.
    if "mios" in request.GET:
        mios = request.GET.get("mios") == "1"
    else:
        mios = vista != "gestionados" and servicios.es_encargado(request.user)
    if mios and empleado:
        qs = servicios.mis_pendientes(qs, request.user) if vista != "gestionados" else qs.filter(gestion__encargado=empleado)
    config = servicios.configuracion()

    contexto = {
        "formularios": qs[:300], "vista": vista, "q": texto, "estado": estado, "vendedor": vendedor,
        "desde": desde, "hasta": hasta, "solo_prioritarios": solo_prioritarios, "mios": mios,
        "estados": FormularioDAMI.Etapa.choices if vista != "gestionados"
        else [(Estado.VENTA_CERRADA, "Venta cerrada"), (Estado.NO_CONCRETADA, "No concretada")],
        "vendedores": Empleado.objects.filter(formularios_dami__isnull=False).distinct().order_by("apellidos", "nombres"),
        "resumen": servicios.resumen_bandeja(), "empleado": empleado,
        "sin_ficha": servicios.sin_ficha(request.user),
        "responsable_filtro": servicios.responsable_filtro(), "responsable_gestion": servicios.responsable_gestion(),
        "faltan_responsables": servicios.es_administrador(request.user)
                               and not (servicios.responsable_filtro() and servicios.responsable_gestion()),
        "form_responsables": ResponsablesForm(initial={"filtro": config.responsable_filtro_id,
                                                       "gestion": config.responsable_gestion_id})
        if servicios.es_administrador(request.user) else None,
    }
    plantilla = "cierre/_bandeja_tabla.html" if _es_htmx(request) else "cierre/bandeja.html"
    return render(request, plantilla, contexto)


# --------------------------------------------------------------------------- detalle y acciones (SCRUM-48, 51, 52, 59)
def _detalle(request, formulario, form_resultado=None, form_correccion=None, status=200):
    usuario = request.user
    gestion = getattr(formulario, "gestion", None) if formulario.estado in (Estado.VENTA_CERRADA, Estado.NO_CONCRETADA) else None
    puede_actuar = usuario.has_perm(GESTIONAR) and servicios.puede_actuar(usuario, formulario)
    propio = puede_actuar and servicios.es_encargado(usuario)
    permitidos = servicios.resultados_permitidos(formulario)
    if puede_actuar and form_resultado is None:
        form_resultado = ResultadoForm(initial={"version": formulario.version}, permitidos=permitidos)
    if gestion and servicios.es_administrador(usuario) and usuario.has_perm("cierre.change_gestioncierre") and form_correccion is None:
        form_correccion = CorreccionForm(initial={"resultado": gestion.resultado, "valor_venta": gestion.valor_venta,
                                                  **inicial_motivo(gestion.motivo_no_concretada)})
    tarjeta = getattr(formulario, "tarjeta", None)
    numero_tarjeta = servicios.numero_tarjeta_visible(usuario, formulario, tarjeta)
    respuesta = render(request, "cierre/formulario.html", {
        "f": formulario,
        "tarjeta": tarjeta,
        "numero_tarjeta": numero_tarjeta,
        "registro": getattr(formulario, "registro_produccion", None),
        "gestion": gestion,
        "historial": servicios.historial(formulario),
        "propio": propio,
        "puede_actuar": puede_actuar,
        "a_cargo": servicios.a_cargo(formulario),
        "puede_pasar_filtro": puede_actuar and formulario.etapa == FormularioDAMI.Etapa.FILTRO,
        "puede_devolver_cierre": puede_actuar and formulario.estado == Estado.EN_GESTION
                                 and formulario.etapa == FormularioDAMI.Etapa.GESTION,
        "responsable_filtro": servicios.responsable_filtro(), "responsable_gestion": servicios.responsable_gestion(),
        "form_pase": NotaPaseForm(initial={"version": formulario.version}),
        "puede_reasignar": formulario.estado in servicios.EN_COLA and servicios.es_administrador(usuario),
        "form_resultado": form_resultado if puede_actuar else None,
        "solo_descartar": len(permitidos) == 1,
        "form_correccion": form_correccion,
        "form_reasignar": ReasignarForm(initial={"version": formulario.version}),
        "sin_ficha": servicios.sin_ficha(usuario),
    }, status=status)
    if numero_tarjeta:  # el navegador no debe guardar en caché una página con el número completo
        add_never_cache_headers(respuesta)
    return respuesta


def _formulario(pk):
    return get_object_or_404(
        FormularioDAMI.objects.select_related("cliente", "vendedor", "encargado_asignado", "base_comercial")
        .exclude(estado=Estado.BORRADOR), pk=pk)


@login_required
@permission_required(VER, raise_exception=True)
def formulario(request, pk):
    return _detalle(request, _formulario(pk))


def _version(request) -> int:
    try:
        return int(request.POST.get("version", ""))
    except ValueError:
        raise ValidationError("Recargue la página e intente de nuevo.") from None


def _avisar_error(request, error: ValidationError):
    texto = " ".join(error.messages)
    if isinstance(error, servicios.Conflicto):
        messages.warning(request, texto)
    else:
        messages.error(request, texto)


@login_required
@permission_required(GESTIONAR, raise_exception=True)
@require_POST
def accion(request, pk):
    formulario = _formulario(pk)
    accion = request.POST.get("accion")
    try:
        if accion in ("pasar_filtro", "devolver_cierre"):
            form = NotaPaseForm(request.POST)
            if not form.is_valid():
                raise ValidationError(" ".join(e for lista in form.errors.values() for e in lista))
            datos = form.cleaned_data
            if accion == "pasar_filtro":
                formulario = servicios.pasar_filtro(request.user, formulario, datos["version"], datos["nota"])
                messages.success(request, f"{formulario} pasó el filtro: se envió a {formulario.encargado_asignado} para la gestión.")
            else:
                formulario = servicios.devolver_a_cierre(request.user, formulario, datos["version"], datos["nota"])
                messages.success(request, f"{formulario}: gestión exitosa. Se devolvió a {formulario.encargado_asignado} "
                                          "para cerrar la venta.")
            return redirect("cierre:bandeja")
        elif accion == "reasignar":
            form = ReasignarForm(request.POST)
            if not form.is_valid():
                raise ValidationError("Elija el supervisor al que se reasigna.")
            formulario = servicios.reasignar(request.user, formulario, form.cleaned_data["version"], form.cleaned_data["encargado"])
            messages.success(request, f"{formulario} quedó a cargo de {formulario.encargado_asignado}.")
        else:
            messages.error(request, "Acción no válida.")
    except PermissionDenied as error:
        messages.error(request, str(error) or "No puede hacer esa acción.")
    except ValidationError as error:
        _avisar_error(request, error)
    return redirect("cierre:formulario", pk=pk)


@login_required
@require_POST
def responsables(request):
    """Administrador: elige quién hace el filtro y cierre y quién la gestión."""
    form = ResponsablesForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Elija supervisores activos.")
        return redirect("cierre:bandeja")
    try:
        config = servicios.configurar_responsables(request.user, filtro=form.cleaned_data["filtro"],
                                                   gestion=form.cleaned_data["gestion"])
    except PermissionDenied as error:
        raise error
    except ValidationError as error:
        _avisar_error(request, error)
        return redirect("cierre:bandeja")
    messages.success(request, f"Responsables guardados. Filtro y cierre: {config.responsable_filtro or 'sin definir'} · "
                              f"Gestión: {config.responsable_gestion or 'sin definir'}.")
    return redirect("cierre:bandeja")


@login_required
@permission_required(GESTIONAR, raise_exception=True)
@require_POST
def resultado(request, pk):
    formulario = _formulario(pk)
    form = ResultadoForm(request.POST)
    if not form.is_valid():
        return _detalle(request, formulario, form_resultado=form, status=400)
    datos = form.cleaned_data
    if datos["resultado"] not in servicios.resultados_permitidos(formulario):
        form.add_error("resultado", "La venta se cierra en el cierre final, después de la gestión.")
        return _detalle(request, formulario, form_resultado=form, status=400)
    try:
        gestion = servicios.registrar_resultado(
            request.user, formulario, datos["version"], resultado=datos["resultado"], valor_venta=datos["valor_venta"],
            motivo=datos.get("motivo") or "", observaciones=datos.get("observaciones") or "",
        )
    except PermissionDenied as error:
        messages.error(request, str(error) or "Este formulario no está en su etapa.")
        return redirect("cierre:formulario", pk=pk)
    except ValidationError as error:
        if isinstance(error, servicios.Conflicto) or not hasattr(error, "error_dict"):
            _avisar_error(request, error)
            return redirect("cierre:formulario", pk=pk)
        form.add_error(None, error)
        return _detalle(request, formulario, form_resultado=form, status=400)
    messages.success(request, f"{formulario}: {gestion.get_resultado_display().lower()}. "
                              "Se actualizó la hoja de producción del vendedor.")
    return redirect("cierre:bandeja")


@login_required
@permission_required("cierre.change_gestioncierre", raise_exception=True)
@require_POST
def corregir(request, pk):
    formulario = _formulario(pk)
    gestion = get_object_or_404(GestionCierre, formulario=formulario)
    if not servicios.es_administrador(request.user):
        raise PermissionDenied
    form = CorreccionForm(request.POST)
    if not form.is_valid():
        return _detalle(request, formulario, form_correccion=form, status=400)
    datos = form.cleaned_data
    try:
        servicios.corregir_resultado(request.user, gestion, resultado=datos["resultado"], valor_venta=datos["valor_venta"],
                                     motivo=datos.get("motivo") or "", motivo_correccion=datos["motivo_correccion"])
    except ValidationError as error:
        form.add_error(None, error)
        return _detalle(request, formulario, form_correccion=form, status=400)
    messages.success(request, f"Se corrigió el resultado de {formulario}. La hoja de producción quedó actualizada.")
    return redirect("cierre:formulario", pk=pk)


# --------------------------------------------------------------------------- metas (SCRUM-55)
@login_required
@permission_required("cierre.view_metamensual", raise_exception=True)
def metas(request):
    anio, mes = _mes_de(request)
    puede_editar = request.user.has_perm("cierre.add_metamensual") and request.user.has_perm("cierre.change_metamensual")
    errores = {}
    if request.method == "POST":
        if not puede_editar:
            raise PermissionDenied
        try:
            anio, mes = int(request.POST.get("anio", anio)), int(request.POST.get("mes", mes))
            date(anio, mes, 1)
        except ValueError:
            raise PermissionDenied("Mes no válido.") from None
        destino = f"{request.path}?mes={anio}-{mes:02d}"
        if "copiar" in request.POST:
            total = servicios.copiar_metas_mes_anterior(request.user, anio, mes)
            messages.success(request, f"Se copiaron {total} meta{'s' if total != 1 else ''} del mes anterior."
                             if total else "No había metas del mes anterior para copiar (o ya estaban definidas).")
            return redirect(destino)
        montos = {}
        for clave, valor in request.POST.items():
            if not clave.startswith("meta-") or not valor.strip():
                continue
            try:
                monto = Decimal(valor.replace(",", ".")).quantize(Decimal("0.01"))
                if monto <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                errores[int(clave[5:])] = "Ingrese un valor mayor a cero."
                continue
            montos[int(clave[5:])] = monto
        if not errores:
            total = servicios.guardar_metas(request.user, anio, mes, montos)
            messages.success(request, f"Metas guardadas ({total} cambio{'s' if total != 1 else ''}).")
            return redirect(destino)
        messages.error(request, "Revise los valores marcados.")
    filas = servicios.metas_del_mes(anio, mes)
    for fila in filas:
        fila["error"] = errores.get(fila["vendedor"].pk)
        fila["valor"] = request.POST.get(f"meta-{fila['vendedor'].pk}") if request.method == "POST" else None
    return render(request, "cierre/metas.html", {"filas": filas, "puede_editar": puede_editar, "anio": anio, "mes": mes,
                                                 **_navegacion_mes(anio, mes)})


# --------------------------------------------------------------------------- panel comercial (SCRUM-54, 56, 57, 58)
@login_required
@permission_required("cierre.view_metamensual", raise_exception=True)
def panel(request):
    anio, mes = _mes_de(request)
    return render(request, "cierre/panel.html", {
        "panel": servicios.panel_comercial(anio, mes, request.user),
        "criterio": servicios.CRITERIO_RENDIMIENTO,
        **_navegacion_mes(anio, mes),
    })


@login_required
@permission_required("cierre.view_metamensual", raise_exception=True)
@require_POST
def ocultar_valores(request):
    servicios.cambiar_ocultar_valores(request.user, request.POST.get("ocultar") == "1")
    mes = request.POST.get("mes", "")
    destino = reverse("cierre:panel")
    return redirect(f"{destino}?mes={mes}" if re.fullmatch(r"\d{4}-\d{2}", mes) else destino)


# --------------------------------------------------------------------------- tablero de ventas (TV del call)
DESTACAR = {"mejor": "Mejor vendedor", "venta": "Venta más alta", "nada": "Sin destacar"}


@login_required
@permission_required("cierre.view_metamensual", raise_exception=True)
def tablero_ventas(request):
    """
    Pantalla para proyectar en la TV: lo que lleva vendido cada vendedor en el mes. Se actualiza
    sola cada minuto (HTMX). ?destacar=mejor|venta|nada elige qué se muestra en grande.
    """
    anio, mes = _mes_de(request)
    destacar = request.GET.get("destacar", "mejor")
    if destacar not in DESTACAR:
        destacar = "mejor"
    hoy = timezone.localdate()
    contexto = {
        **servicios.tablero_ventas(anio, mes, request.user), **_navegacion_mes(anio, mes),
        "destacar": destacar, "opciones_destacar": DESTACAR, "es_mes_actual": (anio, mes) == (hoy.year, hoy.month),
        "actualizado": timezone.localtime(),
    }
    plantilla = "cierre/_tablero_ventas.html" if request.headers.get("HX-Request") == "true" else "cierre/tablero_ventas.html"
    return render(request, plantilla, contexto)
