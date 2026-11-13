import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core import validadores

from . import bases_excel, servicios, tarjetas
from . import bin as bines
from .forms import (AsignarBaseForm, BaseComercialForm, CargarBaseForm, EntidadEmisoraForm, FormularioDAMIForm, ImportarBINForm,
                    RangoBINForm, SolicitudBaseForm)
from .models import (BaseComercial, ContactoRepetido, EntidadEmisora, FormularioDAMI, RangoBIN, RegistroBase, RegistroProduccion,
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


def _guardar(request, form, formulario=None, para=None, contacto=None):
    enviar = "enviar" in request.POST
    try:
        f = servicios.guardar_formulario(request.user, form.cleaned_data, formulario, enviar=enviar, para=para, contacto=contacto)
    except ValidationError as error:
        form.add_error("numero_tarjeta" if "tarjeta" in " ".join(error.messages) else None, error)
        return None
    if f.ayudado_por_id and f.ayudado_por_id == servicios.empleado_de(request.user).pk:
        dueno = f"{f.vendedor.nombres} {f.vendedor.apellidos}"
        if enviar:
            messages.success(request, f"{f} enviado a cierre a nombre de {dueno}: cuenta en su hoja de producción.")
            return redirect("ventas:mis_contactos")
        messages.success(request, f"{f} guardado como borrador a nombre de {dueno}.")
        return redirect("ventas:formulario_detalle", pk=f.pk)
    if enviar:
        messages.success(request, f"{f} enviado a los supervisores. Quedó registrado en su hoja de producción.")
        return redirect("ventas:hoja")
    messages.success(request, f"{f} guardado como borrador.")
    return redirect("ventas:formulario_detalle", pk=f.pk)


@login_required
@permission_required("ventas.add_formulariodami", raise_exception=True)
def formulario_nuevo(request):
    empleado = _solo_vendedores(request)
    form = _form_dami(request, request.POST or None)
    # Desde «Mis contactos»: se prellenan los datos del cliente (si el contacto es suyo o si aceptó ayudar con él).
    contacto_id = (request.POST.get("contacto") if request.method == "POST" else request.GET.get("contacto")) or ""
    contacto = ayuda = None
    if contacto_id.isdigit():
        contacto = RegistroBase.objects.select_related("vendedor").filter(pk=int(contacto_id), vendedor=empleado).first()
        if contacto is None and (ayuda := servicios.ayuda_aceptada(empleado, int(contacto_id))):
            contacto = ayuda.contacto
    if request.method == "GET" and contacto:
        form.initial.update(servicios.datos_dami_de_contacto(contacto))
    if request.method == "POST" and form.is_valid():
        # Si es una ayuda, el DAMI queda a nombre del asesor que pidió la ayuda (dueño del cliente).
        respuesta = _guardar(request, form, para=ayuda.solicitante if ayuda else None, contacto=contacto)
        if respuesta:
            return respuesta
    return render(request, "ventas/formulario_form.html", {
        "form": form, "nuevo": True, "contacto": contacto, "ayuda": ayuda,
        "asesor": f"{ayuda.solicitante.nombres} {ayuda.solicitante.apellidos}" if ayuda else None,
    })


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
    return render(request, "ventas/formulario_form.html", {
        "form": form, "nuevo": False, "formulario": formulario,
        "asesor": f"{formulario.vendedor.nombres} {formulario.vendedor.apellidos}",
        # El aviso «como ayuda» solo lo ve quien lo está llenando para otro (no el dueño del cliente).
        "ayudante": formulario.ayudado_por if formulario.ayudado_por_id == servicios.empleado_de(request.user).pk else None,
    })


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
            messages.success(request, f"{formulario} enviado a los supervisores.")
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
                messages.success(request, "Solicitud enviada. Los supervisores ya fueron notificados.")
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
                    repetidos = base.repetidos.count()
                    if repetidos:
                        messages.warning(request, f"Se encontraron {repetidos} contactos repetidos: no se cargaron a la base y "
                                                  "quedaron en «Repetidos» para que los revise.")
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
        "repetidos_por_revisar": servicios.repetidos_por_revisar(),
    })


def _solo_gestion_bases(request):
    if servicios.es_vendedor(request.user) or not request.user.has_perm("ventas.change_solicitudbase"):
        raise PermissionDenied("Esta sección es para supervisores y administradores.")


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
        "repetidos": base.repetidos.count(),
        "repetidos_por_revisar": base.repetidos.filter(estado=ContactoRepetido.Estado.POR_REVISAR).count(),
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
    totales = {k: sum(f[k] for f in filas) for k in ("enviados", "gestionados", "pendientes", "no_contesto", "no_quiere", "no_entregada", "dami_sacado", "contestaron")}
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
def _resumen_contactos(empleado) -> dict:
    resumen = servicios.seguimiento(vendedor=empleado)
    return {"resumen": resumen,
            "totales": {k: sum(f[k] for f in resumen) for k in ("enviados", "gestionados", "pendientes", "no_contesto", "no_quiere", "no_entregada", "dami_sacado", "contestaron")}}


@login_required
@permission_required("ventas.view_registrobase", raise_exception=True)
def mis_contactos(request):
    empleado = _solo_vendedores(request)
    qs = servicios.contactos_del_vendedor(empleado)
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
    ayudas = servicios.ayudas_de(empleado)
    _anotar_contactos(pagina.object_list, empleado, ayudas["en_curso"])
    return render(request, "ventas/mis_contactos.html", {
        "pagina": pagina, "estado": estado, "base_id": base_id, "q": texto, "estados": RegistroBase.Estado.choices,
        "ayudas_pendientes": ayudas["pendientes"], "asesores": servicios.asesores_para_ayuda(empleado),
        **_resumen_contactos(empleado),
    })


def _anotar_contactos(registros, empleado, en_curso: dict) -> None:
    """Para cada fila: si es un contacto en el que ayuda («cliente de»), la ayuda en curso y su DAMI, si los hay."""
    damis = servicios.damis_de_contactos(registros)
    for r in registros:
        r.es_ayuda = r.vendedor_id != empleado.pk
        r.ayuda = en_curso.get(r.pk)
        r.dami = damis.get(r.pk)


@login_required
@permission_required("ventas.change_registrobase", raise_exception=True)
@require_POST
def contacto_categorizar(request, pk):
    """
    HTMX: el vendedor marca al cliente. Si con la nueva categoría el contacto ya no corresponde al
    filtro de la lista (p. ej. «Por gestionar»), se devuelve un aviso con «Deshacer» que se va solo;
    si corresponde, la fila actualizada. En ambos casos se actualizan los contadores de arriba.
    """
    anterior = RegistroBase.objects.filter(pk=pk).values_list("estado", flat=True).first()
    try:
        registro = servicios.categorizar(request.user, pk, request.POST.get("estado", ""))
    except ValidationError as error:
        return HttpResponse(" ".join(error.messages), status=400)
    if _es_htmx(request):
        empleado = servicios.empleado_de(request.user)
        filtro = request.POST.get("filtro", "")
        filtro = filtro if filtro in RegistroBase.Estado.values else ""
        _anotar_contactos([registro], empleado, servicios.ayudas_de(empleado)["en_curso"])
        contexto = {"r": registro, "recien": True, "estado": filtro, "volver": _volver(request, reverse("ventas:mis_contactos")),
                    "anterior": anterior if anterior != registro.estado else "", "asesores": servicios.asesores_para_ayuda(empleado)}
        plantilla = "ventas/_contacto_hecho.html" if filtro and registro.estado != filtro else "ventas/_fila_contacto.html"
        fila = render(request, plantilla, contexto).content.decode()
        # Contadores de quien está viendo la página (no del dueño del contacto, si es una ayuda).
        resumen = render(request, "ventas/_resumen_contactos.html", {"oob": True, **_resumen_contactos(empleado)})
        return HttpResponse(fila + resumen.content.decode())
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


# --------------------------------------------------------------------------- base de repetidos
def _filtro_repetidos(request):
    base_id = request.GET.get("base") or request.POST.get("base") or ""
    base = BaseComercial.objects.filter(pk=base_id).first() if base_id.isdigit() else None
    estado = request.GET.get("estado", ContactoRepetido.Estado.POR_REVISAR)
    qs = ContactoRepetido.objects.select_related("base", "original__base", "original__vendedor", "revisado_por")
    if base:
        qs = qs.filter(base=base)
    if estado in ContactoRepetido.Estado.values:
        qs = qs.filter(estado=estado)
    else:
        estado = ""
    return qs, base, estado


def _coincide_con(r) -> str:
    o = r.original
    if o is None:
        return ""
    quien = f" · {o.vendedor.apellidos} {o.vendedor.nombres} ({o.get_estado_display()})" if o.vendedor_id else " · sin enviar"
    return f"{o.base.nombre}, fila {o.fila}{quien}"


@login_required
@permission_required("ventas.view_contactorepetido", raise_exception=True)
def repetidos(request):
    """Base de repetidos: contactos que no entraron a su base porque la persona o el teléfono ya estaban cargados."""
    _solo_gestion_bases(request)
    if request.method == "POST":
        if not request.user.has_perm("ventas.change_contactorepetido"):
            raise PermissionDenied
        if "buscar" in request.POST:
            r = servicios.buscar_repetidos(request.user)
            if r["movidos"]:
                messages.warning(request, f"Se encontraron {r['movidos']} contactos repetidos y se pasaron a esta lista.")
            else:
                messages.success(request, "No hay contactos repetidos nuevos en las bases.")
            if r["ya_enviados"]:
                messages.info(request, f"{r['ya_enviados']} repetidos ya estaban enviados a vendedores y se dejaron donde están.")
        elif "descartar_todos" in request.POST:
            _, base, _ = _filtro_repetidos(request)
            n = servicios.descartar_repetidos(request.user, base)
            messages.success(request, f"Se descartaron {n} contactos repetidos.")
        else:
            raise PermissionDenied
        return redirect(_volver(request, "ventas:repetidos"))

    qs, base, estado = _filtro_repetidos(request)
    if request.GET.get("excel") == "1":
        extra = [("BASE", 22, lambda r: r.base.nombre), ("FILA", 7, lambda r: r.fila),
                 ("MOTIVO", 28, lambda r: r.get_motivo_display()), ("COINCIDE POR", 15, lambda r: r.get_criterio_display()),
                 ("COINCIDE CON", 48, _coincide_con), ("REVISION", 20, lambda r: r.get_estado_display())]
        contenido = bases_excel.excel(qs.order_by("base__nombre", "fila"), extra=extra, titulo_hoja="Repetidos")
        return _archivo_excel(contenido, f"Repetidos{' - ' + base.nombre if base else ''}.xlsx")
    pagina = Paginator(qs, 50).get_page(request.GET.get("pagina"))
    return render(request, "ventas/repetidos.html", {
        "pagina": pagina, "base": base, "estado": estado, "estados": ContactoRepetido.Estado.choices,
        "por_revisar": servicios.repetidos_por_revisar(),
        "bases": BaseComercial.objects.filter(repetidos__isnull=False).distinct().order_by("nombre"),
    })


@login_required
@permission_required("ventas.change_contactorepetido", raise_exception=True)
@require_POST
def repetido_revisar(request, pk):
    _solo_gestion_bases(request)
    try:
        r = servicios.revisar_repetido(request.user, pk, request.POST.get("accion", ""))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        if r.estado == ContactoRepetido.Estado.RECUPERADO:
            messages.success(request, f"{r.nombre or r.numero} volvió a la base «{r.base.nombre}» como contacto sin enviar.")
        else:
            messages.success(request, f"{r.nombre or r.numero}: descartado.")
    return redirect(_volver(request, "ventas:repetidos"))


# --------------------------------------------------------------------------- mis estadísticas (vendedor)
@login_required
@permission_required("ventas.view_formulariodami", raise_exception=True)
def mis_estadisticas(request):
    """El vendedor ve su propio crecimiento: DAMI sacados, ventas y monto por mes, frente a meses anteriores."""
    from apps.reportes import graficas

    empleado = _solo_vendedores(request)
    meses = 12 if request.GET.get("meses") == "12" else 6
    datos = servicios.mis_estadisticas(empleado, meses)
    etiquetas = [m.etiqueta for m in datos["serie"]]
    resaltar = len(etiquetas) - 1
    promedio = datos["promedio_3"]
    return render(request, "ventas/mis_estadisticas.html", datos | {
        "grafica_cantidades": graficas.barras_mensuales(
            etiquetas, [("DAMI sacados", graficas.SERIE_1, [m.enviados for m in datos["serie"]]),
                        ("Ventas cerradas", graficas.SERIE_2, [m.cerradas for m in datos["serie"]])],
            titulo="DAMI sacados y ventas cerradas por mes",
            descripcion="Por cada mes, cuántos DAMI envió a cierre y cuántos terminaron en venta cerrada.", resaltar=resaltar),
        "grafica_monto": graficas.barras_mensuales(
            etiquetas, [("Monto vendido", graficas.SERIE_1, [m.total for m in datos["serie"]])],
            titulo="Monto vendido por mes", descripcion="Total de las ventas cerradas de cada mes.", moneda=True,
            resaltar=resaltar, referencia=(promedio, "Tu promedio de 3 meses") if promedio else None),
        "color_dami": graficas.SERIE_1, "color_ventas": graficas.SERIE_2,
    })


# --------------------------------------------------------------------------- ayuda entre asesores con un contacto
@login_required
@permission_required("ventas.add_solicitudayuda", raise_exception=True)
@require_POST
def pedir_ayuda(request, pk):
    _solo_vendedores(request)
    try:
        s = servicios.pedir_ayuda(request.user, pk, request.POST.get("ayudante", ""), request.POST.get("mensaje", ""))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, f"Se le pidió ayuda a {s.ayudante.nombres} {s.ayudante.apellidos} con "
                                  f"{s.contacto.nombre or s.contacto.numero}. Te avisaremos cuando responda.")
    return redirect(_volver(request, "ventas:mis_contactos"))


@login_required
@permission_required("ventas.change_solicitudayuda", raise_exception=True)
@require_POST
def responder_ayuda(request, pk):
    _solo_vendedores(request)
    aceptar = request.POST.get("accion") == "aceptar"
    try:
        s = servicios.responder_ayuda(request.user, pk, aceptar)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        dueno = f"{s.solicitante.nombres} {s.solicitante.apellidos}"
        messages.success(request, f"Aceptaste ayudar a {dueno}: el cliente ya está en tu lista como «cliente de {dueno}». "
                                  "Si sacas el DAMI, queda a su nombre." if aceptar else f"Le avisamos a {dueno} que no puedes ayudar.")
    return redirect(_volver(request, "ventas:mis_contactos"))


@login_required
@permission_required("ventas.change_solicitudayuda", raise_exception=True)
@require_POST
def cancelar_ayuda(request, pk):
    _solo_vendedores(request)
    try:
        servicios.cancelar_ayuda(request.user, pk)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Se retiró la petición de ayuda.")
    return redirect(_volver(request, "ventas:mis_contactos"))
