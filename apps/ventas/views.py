import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core import validadores

from . import bases_excel, servicios, tarjetas
from . import bin as bines
from .forms import (AsignarBaseForm, BaseComercialForm, CargarBaseForm, EntidadEmisoraForm, FormularioDAMIForm, ImportarBINForm,
                    RangoBINForm, SolicitudBaseForm)
from .models import (BaseComercial, EntidadEmisora, FormularioDAMI, RangoBIN, RegistroBase, RegistroProduccion,
                     SolicitudBase)

Estado = FormularioDAMI.Estado


def _es_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _solo_vendedores(request):
    if not servicios.es_vendedor(request.user):
        raise PermissionDenied("Esta sección es para vendedores con ficha de empleado.")
    return servicios.empleado_de(request.user)


def _solo_administradores(request):
    if not servicios.es_administrador(request.user):
        raise PermissionDenied("Esta sección es para administradores.")


# --------------------------------------------------------------------------- inicio del módulo / panel (SCRUM-45)
@login_required
@permission_required("ventas.view_formulariodami", raise_exception=True)
def inicio(request):
    if not servicios.es_vendedor(request.user):
        return redirect("ventas:formularios")
    empleado = servicios.empleado_de(request.user)
    hoy = timezone.localdate()
    return render(request, "ventas/panel.html", {
        "panel": servicios.panel_vendedor(empleado, hoy),
        "hoy": hoy,
    })


# --------------------------------------------------------------------------- formularios DAMI (SCRUM-38 a 42)
@login_required
@permission_required("ventas.view_formulariodami", raise_exception=True)
def formularios(request):
    qs = servicios.formularios_visibles(request.user).order_by("-actualizado_en")
    texto = request.GET.get("q", "").strip()
    if texto:
        ident = validadores.normalizar_identificacion(texto)
        filtro = Q(cliente__identificacion__icontains=ident) | Q(cliente__ruc__icontains=ident) | Q(cliente__ciudad__icontains=texto)
        if texto.upper().startswith("DAMI-") and texto[5:].isdigit():
            filtro |= Q(pk=int(texto[5:]))
        qs = qs.filter(filtro)
    estado = request.GET.get("estado", "")
    if estado in Estado.values:
        qs = qs.filter(estado=estado)
    desde, hasta = parse_date(request.GET.get("desde") or ""), parse_date(request.GET.get("hasta") or "")
    if desde:
        qs = qs.filter(creado_en__date__gte=desde)
    if hasta:
        qs = qs.filter(creado_en__date__lte=hasta)
    contexto = {"formularios": qs[:300], "q": texto, "estado": estado, "estados": Estado.choices,
                "desde": desde, "hasta": hasta, "es_vendedor": servicios.es_vendedor(request.user)}
    plantilla = "ventas/_formularios_tabla.html" if _es_htmx(request) else "ventas/formularios.html"
    return render(request, plantilla, contexto)


def _form_dami(request, data=None, formulario=None):
    return FormularioDAMIForm(data, tarjeta_guardada=getattr(formulario, "tarjeta", None) if formulario else None)


def _guardar(request, form, formulario=None):
    enviar = "enviar" in request.POST
    try:
        f = servicios.guardar_formulario(request.user, form.cleaned_data, formulario, enviar=enviar)
    except ValidationError as error:
        form.add_error("numero_tarjeta" if "tarjeta" in " ".join(error.messages) else None, error)
        return None
    if enviar:
        messages.success(request, f"{f} enviado a los encargados de cierre. Quedó registrado en su hoja de producción.")
        return redirect("ventas:hoja")
    messages.success(request, f"{f} guardado como borrador.")
    return redirect("ventas:formulario_detalle", pk=f.pk)


@login_required
@permission_required("ventas.add_formulariodami", raise_exception=True)
def formulario_nuevo(request):
    empleado = _solo_vendedores(request)
    form = _form_dami(request, request.POST or None)
    contacto_id = request.GET.get("contacto", "")
    if request.method == "GET" and contacto_id.isdigit():
        # Desde «Mis contactos»: se prellenan los datos del cliente de la base (solo si el contacto es suyo).
        contacto = RegistroBase.objects.filter(pk=int(contacto_id), vendedor=empleado).first()
        if contacto:
            form.initial.update(servicios.datos_dami_de_contacto(contacto))
    if request.method == "POST" and form.is_valid():
        respuesta = _guardar(request, form)
        if respuesta:
            return respuesta
    return render(request, "ventas/formulario_form.html", {"form": form, "nuevo": True})


@login_required
@permission_required("ventas.change_formulariodami", raise_exception=True)
def formulario_editar(request, pk):
    if (respuesta := _ya_enviado(request, pk)):
        return respuesta
    formulario = get_object_or_404(servicios.formularios_visibles(request.user), pk=pk)
    if not servicios.formulario_editable(request.user, formulario):
        messages.error(request, "Este formulario ya fue enviado o finalizado y no se puede modificar.")
        return redirect("ventas:formulario_detalle", pk=pk)
    if request.method == "POST":
        form = _form_dami(request, request.POST, formulario)
        if form.is_valid():
            respuesta = _guardar(request, form, formulario)
            if respuesta:
                return respuesta
    else:
        form = FormularioDAMIForm.desde_formulario(formulario)
    return render(request, "ventas/formulario_form.html", {"form": form, "nuevo": False, "formulario": formulario})


@login_required
@permission_required("ventas.view_formulariodami", raise_exception=True)
def formulario_detalle(request, pk):
    if (respuesta := _ya_enviado(request, pk)):
        return respuesta
    formulario = get_object_or_404(servicios.formularios_visibles(request.user), pk=pk)
    return render(request, "ventas/formulario_detalle.html", {
        "f": formulario,
        "historial": _historial(formulario),
        "registro": getattr(formulario, "registro_produccion", None),
        "tarjeta": getattr(formulario, "tarjeta", None),
        "editable": servicios.formulario_editable(request.user, formulario),
        "otros_en_curso": servicios.dami_en_curso(formulario.cliente, excluir=formulario),
    })


def _ya_enviado(request, pk):
    """El vendedor no vuelve a abrir un DAMI enviado: solo queda en su hoja de producción."""
    empleado = servicios.empleado_de(request.user)
    if servicios.es_vendedor(request.user) and FormularioDAMI.objects.filter(
            pk=pk, vendedor=empleado).exclude(estado=Estado.BORRADOR).exists():
        messages.info(request, "Ese DAMI ya fue enviado: ya no se puede abrir. Su resultado se ve en la hoja de producción.")
        return redirect("ventas:hoja")
    return None


def _historial(formulario):
    from apps.core.models import Usuario

    filas = list(formulario.historial.order_by("fecha", "pk"))
    nombres = {u.pk: u.get_full_name() or u.username for u in Usuario.objects.filter(pk__in={h.usuario_id for h in filas})}
    etiquetas = dict(Estado.choices)
    return [{"estado": h.estado_nuevo, "texto": etiquetas.get(h.estado_nuevo, h.estado_nuevo), "fecha": h.fecha,
             "usuario": nombres.get(h.usuario_id, "Sistema")} for h in filas]


@login_required
@permission_required("ventas.change_formulariodami", raise_exception=True)
@require_POST
def formulario_accion(request, pk):
    formulario = get_object_or_404(servicios.formularios_visibles(request.user), pk=pk)
    accion = request.POST.get("accion")
    try:
        if accion == "enviar":
            servicios.enviar_formulario(request.user, formulario)
            messages.success(request, f"{formulario} enviado a los encargados de cierre.")
        elif accion == "anular":
            servicios.anular_formulario(request.user, formulario)
            messages.success(request, f"{formulario} anulado.")
        else:
            messages.error(request, "Acción no válida.")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    return redirect("ventas:formulario_detalle", pk=pk)


@login_required
@permission_required("ventas.add_formulariodami", raise_exception=True)
def buscar_cliente(request):
    """HTMX (SCRUM-39): si el cliente ya existe, avisa y llena sus datos en el formulario."""
    cedula = validadores.normalizar_identificacion(request.GET.get("cedula", ""))
    ruc = validadores.normalizar_identificacion(request.GET.get("ruc", ""))
    contexto, campos = {}, None
    if cedula and not validadores.cedula_valida(cedula):
        contexto = {"estado": "invalida", "mensaje": "La cédula no es válida. Revise que tenga 10 dígitos y que estén bien escritos."}
    elif cedula or len(ruc) == 13:
        cliente = servicios.buscar_cliente(cedula, ruc)
        if cliente:
            contexto = {"estado": "existe", "cliente": cliente, "en_curso": servicios.dami_en_curso(cliente)}
            campos = {k: getattr(cliente, k) for k in servicios.CAMPOS_CLIENTE} | {
                "cedula": cliente.cedula, "ruc": cliente.ruc_mostrar}
        else:
            contexto = {"estado": "nuevo"}
    respuesta = render(request, "ventas/_cliente_estado.html", contexto)
    if campos:
        respuesta["HX-Trigger"] = json.dumps({"clienteEncontrado": {"campos": campos}})
    return respuesta


# --------------------------------------------------------------------------- hoja de producción (SCRUM-43, 44)
def _mes_pedido(request):
    """Lee ?mes=AAAA-MM (si no es válido o es futuro, el mes en curso) y arma la navegación entre meses."""
    hoy = timezone.localdate()
    try:
        anio, mes = (int(x) for x in request.GET.get("mes", "").split("-"))
        pedido = date(anio, mes, 1)
    except (ValueError, OverflowError):
        pedido = hoy
    if pedido > hoy:
        pedido = hoy
    anio, mes = pedido.year, pedido.month
    anterior = (anio - 1, 12) if mes == 1 else (anio, mes - 1)
    siguiente = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return anio, mes, {
        "mes_actual": date(anio, mes, 1),
        "anterior": f"{anterior[0]}-{anterior[1]:02d}",
        "siguiente": f"{siguiente[0]}-{siguiente[1]:02d}" if date(*siguiente, 1) <= hoy.replace(day=1) else None,
    }


@login_required
@permission_required("ventas.view_hojaproduccion", raise_exception=True)
def hoja(request):
    empleado = _solo_vendedores(request)
    anio, mes, navegacion = _mes_pedido(request)
    return render(request, "ventas/hoja.html", servicios.hoja_del_mes(empleado, anio, mes) | navegacion)


@login_required
@permission_required("ventas.view_hojaproduccion", raise_exception=True)
def hojas_vendedores(request):
    """Administrador: listado de vendedores con sus totales del mes."""
    _solo_administradores(request)
    anio, mes, navegacion = _mes_pedido(request)
    return render(request, "ventas/hojas_vendedores.html", {"filas": servicios.hojas_del_mes(anio, mes)} | navegacion)


@login_required
@permission_required("ventas.view_hojaproduccion", raise_exception=True)
def hoja_vendedor(request, pk):
    """Administrador: la hoja de un vendedor, solo lectura (la aprobación la escribe el vendedor)."""
    _solo_administradores(request)
    empleado = get_object_or_404(servicios.vendedores_con_hoja(), pk=pk)
    anio, mes, navegacion = _mes_pedido(request)
    return render(request, "ventas/hoja.html", servicios.hoja_del_mes(empleado, anio, mes) | navegacion | {"solo_lectura": True})


@login_required
@permission_required("ventas.change_registroproduccion", raise_exception=True)
@require_POST
def hoja_aprobacion(request, pk):
    """HTMX: guarda la aprobación al salir de la celda y devuelve la celda actualizada."""
    try:
        registro = servicios.guardar_aprobacion(request.user, pk, request.POST.get("aprobacion", ""))
        contexto = {"r": registro, "guardado": True}
    except ValidationError as error:
        registro = get_object_or_404(RegistroProduccion, pk=pk, hoja__vendedor=servicios.empleado_de(request.user))
        contexto = {"r": registro, "error": " ".join(error.messages), "valor": request.POST.get("aprobacion", "")}
        if registro.aprobacion:  # ya estaba fija: se muestra bloqueada con el valor guardado
            contexto = {"r": registro, "error": contexto["error"]}
    return render(request, "ventas/_celda_aprobacion.html", contexto)


# --------------------------------------------------------------------------- validación de tarjetas
@login_required
@permission_required("ventas.view_rangobin", raise_exception=True)
def validar_tarjeta(request):
    """
    GET: la pantalla. POST (HTMX, mientras se escribe): el resultado.
    El número viaja por POST (nunca en la URL), no se guarda y la respuesta solo lo muestra enmascarado.
    """
    if request.method == "POST":
        resultado = bines.analizar(request.POST.get("numero", ""), request.POST.get("caducidad", ""))
        return render(request, "ventas/_resultado_tarjeta.html", {"r": resultado})
    return render(request, "ventas/validar_tarjeta.html", {
        "r": bines.Resultado(),
        "marcas": tarjetas.MARCAS.values(),
        "total_bins": RangoBIN.objects.count(),
        "total_entidades": EntidadEmisora.objects.filter(activa=True).count(),
    })


@login_required
@permission_required("ventas.view_rangobin", raise_exception=True)
def catalogo_bin(request):
    puede_editar = request.user.has_perm("ventas.add_rangobin")
    form_bin, form_entidad, form_importar = RangoBINForm(prefix="b"), EntidadEmisoraForm(prefix="e"), ImportarBINForm(prefix="i")
    if request.method == "POST":
        if not puede_editar:
            raise PermissionDenied
        if "agregar_bin" in request.POST:
            form_bin = RangoBINForm(request.POST, prefix="b")
            if form_bin.is_valid():
                rango = form_bin.save(commit=False)
                rango.registrado_por = request.user
                rango.save()
                messages.success(request, f"BIN {rango.bin} agregado: {rango.entidad}.")
                return redirect("ventas:catalogo_bin")
        elif "agregar_entidad" in request.POST and request.user.has_perm("ventas.add_entidademisora"):
            form_entidad = EntidadEmisoraForm(request.POST, prefix="e")
            if form_entidad.is_valid():
                entidad = form_entidad.save()
                messages.success(request, f"«{entidad}» agregada al catálogo.")
                return redirect("ventas:catalogo_bin")
        elif "importar" in request.POST:
            form_importar = ImportarBINForm(request.POST, request.FILES, prefix="i")
            if form_importar.is_valid():
                r = bines.importar_csv(form_importar.cleaned_data["archivo"], request.user)
                messages.success(request, f"Importación: {r.creados} BIN nuevos y {r.actualizados} actualizados.")
                for error in r.errores[:15]:
                    messages.warning(request, error)
                if len(r.errores) > 15:
                    messages.warning(request, f"…y {len(r.errores) - 15} filas más con errores.")
                return redirect("ventas:catalogo_bin")
        else:
            raise PermissionDenied

    rangos = RangoBIN.objects.select_related("entidad").order_by("bin")
    texto = request.GET.get("q", "").strip()
    if texto:
        rangos = rangos.filter(Q(bin__startswith=texto) | Q(entidad__nombre__icontains=texto))
    return render(request, "ventas/catalogo_bin.html", {
        "rangos": rangos[:500], "q": texto, "puede_editar": puede_editar,
        "entidades": EntidadEmisora.objects.filter(activa=True).order_by("tipo", "nombre"),
        "form_bin": form_bin, "form_entidad": form_entidad, "form_importar": form_importar,
    })


@login_required
@permission_required("ventas.delete_rangobin", raise_exception=True)
@require_POST
def eliminar_bin(request, pk):
    rango = get_object_or_404(RangoBIN, pk=pk)
    rango.delete()
    messages.success(request, f"BIN {rango.bin} eliminado del catálogo.")
    return redirect("ventas:catalogo_bin")


# --------------------------------------------------------------------------- bases comerciales (SCRUM-46, 47)
@login_required
@permission_required("ventas.view_basecomercial", raise_exception=True)
def bases(request):
    if servicios.es_vendedor(request.user):
        return _bases_vendedor(request)
    if not request.user.has_perm("ventas.change_solicitudbase"):
        raise PermissionDenied
    return _bases_gestion(request)


def _bases_vendedor(request):
    empleado = servicios.empleado_de(request.user)
    form = SolicitudBaseForm(request.POST or None)
    if request.method == "POST":
        if not request.user.has_perm("ventas.add_solicitudbase") or {"crear_base", "asignar"} & set(request.POST):
            raise PermissionDenied
        if form.is_valid():
            try:
                servicios.solicitar_base(request.user, form.cleaned_data["mensaje"])
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
            else:
                messages.success(request, "Solicitud enviada. Los encargados ya fueron notificados.")
            return redirect("ventas:bases")
    resumen = servicios.seguimiento(vendedor=empleado)
    return render(request, "ventas/bases_vendedor.html", {
        "form": form,
        "resumen": resumen,
        "contactos": {k: sum(f[k] for f in resumen) for k in ("enviados", "gestionados", "pendientes")},
        "bases": servicios.bases_del_vendedor(empleado),
        "solicitudes": SolicitudBase.objects.filter(vendedor=empleado).select_related("base_asignada").order_by("-creado_en")[:20],
        "pendiente": SolicitudBase.objects.filter(vendedor=empleado, estado=SolicitudBase.Estado.PENDIENTE).exists(),
    })


def _bases_gestion(request):
    form_base = BaseComercialForm(prefix="b")
    form_asignar = AsignarBaseForm(prefix="a")
    form_cargar = CargarBaseForm(prefix="c")
    if request.method == "POST":
        if "cargar" in request.POST and request.user.has_perm("ventas.add_basecomercial"):
            form_cargar = CargarBaseForm(request.POST, request.FILES, prefix="c")
            if form_cargar.is_valid():
                d = form_cargar.cleaned_data
                try:
                    base = servicios.importar_base(d["nombre"], d["descripcion"], d["archivo"], request.user)
                except ValidationError as error:
                    form_cargar.add_error(None, error)
                else:
                    messages.success(request, f"Base «{base.nombre}» cargada con {base.total_registros} contactos.")
                    return redirect("ventas:base_detalle", pk=base.pk)
        elif "crear_base" in request.POST and request.user.has_perm("ventas.add_basecomercial"):
            form_base = BaseComercialForm(request.POST, prefix="b")
            if form_base.is_valid():
                base = form_base.save(commit=False)
                base.cargada_por = request.user
                base.save()
                messages.success(request, f"Base «{base.nombre}» registrada.")
                return redirect("ventas:bases")
        elif "asignar" in request.POST and request.user.has_perm("ventas.add_asignacionbase"):
            form_asignar = AsignarBaseForm(request.POST, prefix="a")
            if form_asignar.is_valid():
                d = form_asignar.cleaned_data
                libres_antes = RegistroBase.objects.filter(base=d["base"], vendedor__isnull=True).count()
                total = servicios.asignar_base(d["base"], list(d["vendedores"]), request.user, d.get("cantidad"))
                repartidos = libres_antes - RegistroBase.objects.filter(base=d["base"], vendedor__isnull=True).count()
                messages.success(request, f"Base asignada a {total} vendedor{'es' if total != 1 else ''}"
                                 + (" (los demás ya la tenían)" if total < len(d["vendedores"]) else "")
                                 + (f". Se enviaron {repartidos} contactos." if repartidos else "."))
                return redirect(_volver(request, "ventas:bases"))
        else:
            raise PermissionDenied

    return render(request, "ventas/bases_gestion.html", {
        "pendientes": SolicitudBase.objects.filter(estado=SolicitudBase.Estado.PENDIENTE).select_related("vendedor").order_by("creado_en"),
        "respondidas": SolicitudBase.objects.exclude(estado=SolicitudBase.Estado.PENDIENTE)
                       .select_related("vendedor", "base_asignada", "atendida_por").order_by("-atendida_en")[:10],
        "bases": servicios.efectividad_bases(),
        "bases_activas": BaseComercial.objects.filter(activa=True).order_by("nombre"),
        "form_base": form_base,
        "form_asignar": form_asignar,
        "form_cargar": form_cargar,
    })


def _solo_gestion_bases(request):
    if servicios.es_vendedor(request.user) or not request.user.has_perm("ventas.change_solicitudbase"):
        raise PermissionDenied("Esta sección es para encargados de cierre y administradores.")


@login_required
@permission_required("ventas.view_basecomercial", raise_exception=True)
def base_detalle(request, pk):
    """Resumen de la carga, vista previa con el formato de la empresa, reparto y seguimiento de una base."""
    _solo_gestion_bases(request)
    base = get_object_or_404(BaseComercial, pk=pk)
    efectividad = servicios.efectividad_bases(base)
    return render(request, "ventas/base_detalle.html", {
        "base": base, "resumen": base.resumen_carga, "e": efectividad[0] if efectividad else None,
        "muestra": base.registros.select_related("vendedor")[:25],
        "seguimiento": servicios.seguimiento(base=base),
        "form_asignar": AsignarBaseForm(prefix="a", initial={"base": base}),
        "encabezados": bases_excel.ENCABEZADOS,
    })


@login_required
@permission_required("ventas.view_basecomercial", raise_exception=True)
def base_excel(request, pk):
    """La base en el formato de la empresa (con vendedor y estado si se pide ?gestion=1)."""
    _solo_gestion_bases(request)
    base = get_object_or_404(BaseComercial, pk=pk)
    con_gestion = request.GET.get("gestion") == "1"
    contenido = bases_excel.excel(base.registros.select_related("vendedor"), con_gestion=con_gestion)
    return _archivo_excel(contenido, f"{base.nombre}{' - gestion' if con_gestion else ''}.xlsx")


@login_required
@permission_required("ventas.view_basecomercial", raise_exception=True)
def bases_plantilla(request):
    return _archivo_excel(bases_excel.excel([]), "Plantilla de base comercial.xlsx")


def _archivo_excel(contenido: bytes, nombre: str) -> HttpResponse:
    from django.utils.http import content_disposition_header

    respuesta = HttpResponse(contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respuesta["Content-Disposition"] = content_disposition_header(True, nombre)
    return respuesta


@login_required
@permission_required("ventas.view_basecomercial", raise_exception=True)
def bases_seguimiento(request):
    """Encargados y administrador (SCRUM-96): cómo gestionó cada vendedor los contactos que recibió."""
    _solo_gestion_bases(request)
    from apps.personal.models import Empleado

    base = BaseComercial.objects.filter(pk=request.GET.get("base") or 0).first()
    vendedor = Empleado.objects.filter(pk=request.GET.get("vendedor") or 0).first()
    filas = servicios.seguimiento(base=base, vendedor=vendedor)
    totales = {k: sum(f[k] for f in filas) for k in ("enviados", "gestionados", "pendientes", "no_contesto", "no_quiere", "dami_sacado")}
    return render(request, "ventas/bases_seguimiento.html", {
        "filas": filas, "totales": totales, "base": base, "vendedor": vendedor,
        "bases": BaseComercial.objects.order_by("-activa", "nombre"),
        "vendedores": Empleado.objects.filter(contactos_base__isnull=False).distinct().order_by("apellidos", "nombres"),
    })


@login_required
@permission_required("ventas.view_basecomercial", raise_exception=True)
def bases_contactos(request):
    """Detalle de los contactos (de un vendedor y/o una base) con su categoría, para encargados y administrador."""
    _solo_gestion_bases(request)
    from apps.personal.models import Empleado

    qs = RegistroBase.objects.filter(vendedor__isnull=False).select_related("base", "vendedor")
    base = BaseComercial.objects.filter(pk=request.GET.get("base") or 0).first() if (request.GET.get("base") or "").isdigit() else None
    vendedor = Empleado.objects.filter(pk=request.GET.get("vendedor") or 0).first() if (request.GET.get("vendedor") or "").isdigit() else None
    if base:
        qs = qs.filter(base=base)
    if vendedor:
        qs = qs.filter(vendedor=vendedor)
    estado = request.GET.get("estado", "")
    if estado in RegistroBase.Estado.values:
        qs = qs.filter(estado=estado)
    pagina = Paginator(qs.order_by("vendedor__apellidos", "base__nombre", "fila"), 50).get_page(request.GET.get("pagina"))
    return render(request, "ventas/bases_contactos.html", {
        "pagina": pagina, "estado": estado, "estados": RegistroBase.Estado.choices, "base": base, "vendedor": vendedor,
    })


# --------------------------------------------------------------------------- contactos del vendedor (SCRUM-95)
@login_required
@permission_required("ventas.view_registrobase", raise_exception=True)
def mis_contactos(request):
    empleado = _solo_vendedores(request)
    qs = servicios.contactos_del_vendedor(empleado)
    resumen = servicios.seguimiento(vendedor=empleado)
    base_id = request.GET.get("base", "")
    if base_id.isdigit():
        qs = qs.filter(base_id=int(base_id))
    estado = request.GET.get("estado", "PENDIENTE" if "estado" not in request.GET else "")
    if estado in RegistroBase.Estado.values:
        qs = qs.filter(estado=estado)
    texto = request.GET.get("q", "").strip()
    if texto:
        qs = qs.filter(Q(nombre__icontains=texto) | Q(cedula__icontains=texto) | Q(numero__icontains=texto) | Q(ciudad__icontains=texto))
    pagina = Paginator(qs.order_by("base__nombre", "fila"), 30).get_page(request.GET.get("pagina"))
    return render(request, "ventas/mis_contactos.html", {
        "pagina": pagina, "resumen": resumen, "estado": estado, "base_id": base_id, "q": texto,
        "estados": RegistroBase.Estado.choices,
        "totales": {k: sum(f[k] for f in resumen) for k in ("enviados", "gestionados", "pendientes", "no_contesto", "no_quiere", "dami_sacado")},
    })


@login_required
@permission_required("ventas.change_registrobase", raise_exception=True)
@require_POST
def contacto_categorizar(request, pk):
    """HTMX: el vendedor marca al cliente y se devuelve la fila actualizada."""
    try:
        registro = servicios.categorizar(request.user, pk, request.POST.get("estado", ""))
    except ValidationError as error:
        return HttpResponse(" ".join(error.messages), status=400)
    if _es_htmx(request):
        return render(request, "ventas/_fila_contacto.html", {"r": registro, "recien": True})
    messages.success(request, f"{registro.nombre or registro.numero}: {registro.get_estado_display().lower()}.")
    return redirect(_volver(request, "ventas:mis_contactos"))


def _volver(request, defecto: str) -> str:
    """El campo «volver» solo puede llevar a una dirección del propio sistema."""
    volver = request.POST.get("volver", "")
    return volver if url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}) else defecto


@login_required
@permission_required("ventas.change_solicitudbase", raise_exception=True)
@require_POST
def responder_solicitud(request, pk):
    solicitud = get_object_or_404(SolicitudBase, pk=pk)
    try:
        if request.POST.get("accion") == "atender":
            base = get_object_or_404(BaseComercial, pk=request.POST.get("base") or 0)
            cantidad = request.POST.get("cantidad", "")
            cantidad = int(cantidad) if cantidad.isdigit() and int(cantidad) > 0 else None
            servicios.atender_solicitud(solicitud, base, request.user, request.POST.get("respuesta", ""), cantidad)
            recibidos = RegistroBase.objects.filter(base=base, vendedor=solicitud.vendedor).count()
            messages.success(request, f"Se asignó «{base.nombre}» a {solicitud.vendedor}"
                                      + (f" ({recibidos} contactos en total)." if recibidos else "."))
        elif request.POST.get("accion") == "rechazar":
            servicios.rechazar_solicitud(solicitud, request.user, request.POST.get("respuesta", ""))
            messages.success(request, f"Se rechazó la solicitud de {solicitud.vendedor}.")
        else:
            messages.error(request, "Acción no válida.")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    return redirect("ventas:bases")
