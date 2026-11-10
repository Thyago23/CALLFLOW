import json
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.staticfiles import finders
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.agenda.models import Evento
from apps.agenda.servicios import eventos_de
from apps.inventario.models import Material
from apps.personal.models import Empleado

from . import notificaciones as avisos
from .codigos import generar_codigo_acceso, generar_usuario
from .cuentas import CLAVE_SESION_CREDENCIALES, preparar_credenciales
from .forms import CambioClaveForm, IngresoForm, UsuarioCrearForm
from .modulos import grupos_de
from .models import Notificacion, Rol, SuscripcionPush, Usuario


def _es_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


# --------------------------------------------------------------------------- acceso
class IngresoView(auth_views.LoginView):
    form_class = IngresoForm
    redirect_authenticated_user = True


@login_required
@never_cache
def cambiar_clave(request):
    """Cambio de contraseña. Obligatorio cuando la contraseña es temporal."""
    obligatorio = request.user.debe_cambiar_clave
    form = CambioClaveForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        usuario = form.save(commit=False)
        usuario.debe_cambiar_clave = False
        usuario.save()
        update_session_auth_hash(request, usuario)
        messages.success(request, "Contraseña actualizada.")
        return redirect("inicio")
    return render(request, "registration/cambiar_clave.html", {"form": form, "obligatorio": obligatorio})


# --------------------------------------------------------------------------- dashboard
@login_required
def dashboard(request):
    usuario = request.user
    hoy = timezone.localdate()

    # Indicadores: los de "alerta" van al panel de pendientes con lo que hay que hacer; el resto, al resumen.
    indicadores = []
    if usuario.has_perm("personal.view_empleado"):
        indicadores.append({
            "titulo": "Empleados activos", "icono": "people",
            "valor": Empleado.objects.filter(estado=Empleado.Estado.ACTIVO).count(),
            "url": reverse("personal:empleado_lista") + "?estado=ACTIVO",
        })
    if usuario.has_perm("core.view_usuario"):
        indicadores.append({
            "titulo": "Usuarios activos", "icono": "person-badge",
            "valor": Usuario.objects.filter(is_active=True).count(),
            "url": reverse("usuarios"),
        })
    if usuario.has_perm("asistencia.view_novedadasistencia"):
        from apps.asistencia.models import NovedadAsistencia

        indicadores.append({
            "titulo": "Novedades de asistencia por revisar", "icono": "clock-history",
            "valor": NovedadAsistencia.objects.filter(estado=NovedadAsistencia.Estado.PENDIENTE).count(),
            "url": reverse("asistencia:novedades"), "alerta": True, "accion": "Justificar o descontar",
        })
    if usuario.has_perm("inventario.view_material"):
        indicadores.append({
            "titulo": "Materiales con stock bajo", "icono": "box-seam",
            "valor": Material.objects.filter(activo=True, stock_actual__lte=F("stock_minimo")).count(),
            "url": reverse("inventario:materiales") + "?estado=bajo", "alerta": True, "accion": "Conviene reponerlos",
        })
    if usuario.has_perm("cierre.add_gestioncierre"):
        from apps.ventas.models import FormularioDAMI

        indicadores.append({
            "titulo": "Formularios DAMI sin asignar", "icono": "inbox",
            "valor": FormularioDAMI.objects.filter(estado=FormularioDAMI.Estado.ENVIADO).count(),
            "url": reverse("cierre:bandeja") + "?estado=ENVIADO", "alerta": True, "accion": "Esperan a un encargado",
        })
    if usuario.has_perm("ventas.change_solicitudbase"):
        from apps.ventas.servicios import solicitudes_pendientes

        indicadores.append({
            "titulo": "Solicitudes de bases pendientes", "icono": "database",
            "valor": solicitudes_pendientes(), "url": reverse("ventas:bases"), "alerta": True, "accion": "Asignar o rechazar",
        })
    if usuario.has_perm("agenda.view_evento"):
        indicadores.append({
            "titulo": "Mis actividades de hoy", "icono": "calendar3",
            "valor": eventos_de(usuario).filter(estado=Evento.Estado.PENDIENTE, inicio__date=hoy).count(),
            "url": reverse("agenda:calendario") + f"?vista=dia&fecha={hoy.isoformat()}",
        })

    pendientes = [i for i in indicadores if i.get("alerta")]
    hora = timezone.localtime().hour
    return render(request, "core/dashboard.html", {
        "saludo": "Buenos días" if hora < 12 else "Buenas tardes" if hora < 19 else "Buenas noches",
        "accesos": _accesos_rapidos(usuario),
        "resumen": [i for i in indicadores if not i.get("alerta")],
        "pendientes": pendientes,
        "por_atender": sum(1 for i in pendientes if i["valor"]),
        "grupos": grupos_de(usuario),
        "notificaciones": Notificacion.objects.filter(destinatario=usuario).order_by("leida", "-creado_en")[:6],
    })


# (permiso, texto, icono, ruta): las tareas más comunes de cada rol, en orden de prioridad.
_ACCESOS = [
    ("personal.add_empleado", "Registrar empleado", "person-plus", "personal:empleado_crear"),
    ("ventas.add_formulariodami", "Nuevo DAMI", "file-earmark-plus", "ventas:formulario_nuevo"),
    ("cierre.add_gestioncierre", "Bandeja de cierre", "inbox", "cierre:bandeja"),
    ("asistencia.view_registroasistencia", "Asistencia de hoy", "clock-history", "asistencia:tablero"),
    ("nomina.view_periodonomina", "Roles de pago", "cash-coin", "nomina:resumen"),
    ("core.add_usuario", "Nuevo usuario", "person-badge", "usuario_crear"),
    ("agenda.add_evento", "Nueva actividad", "calendar-plus", "agenda:crear"),
]
MAX_ACCESOS = 4


def _accesos_rapidos(usuario) -> list[dict]:
    accesos = []
    for permiso, titulo, icono, ruta in _ACCESOS:
        if permiso == "ventas.add_formulariodami" and not (usuario.rol == Rol.VENDEDOR and hasattr(usuario, "empleado")):
            continue  # solo el vendedor con ficha registra formularios
        if permiso == "cierre.add_gestioncierre" and usuario.rol != Rol.ENCARGADO_CIERRE:
            continue
        if usuario.has_perm(permiso):
            accesos.append({"titulo": titulo, "icono": icono, "url": reverse(ruta)})
    return accesos[:MAX_ACCESOS]


# --------------------------------------------------------------------------- usuarios
@login_required
@permission_required("core.view_usuario", raise_exception=True)
def usuarios(request):
    qs = Usuario.objects.select_related("empleado").order_by("-is_active", "username")
    texto = request.GET.get("q", "").strip()
    if texto:
        filtro = Q(username__icontains=texto) | Q(email__icontains=texto)
        for palabra in texto.split():
            filtro |= Q(first_name__icontains=palabra) | Q(last_name__icontains=palabra)
        qs = qs.filter(filtro)
    rol = request.GET.get("rol", "")
    if rol in Rol.values:
        qs = qs.filter(rol=rol)
    contexto = {"usuarios": qs, "q": texto, "rol": rol, "roles": Rol.choices}
    plantilla = "core/_usuarios_tabla.html" if _es_htmx(request) else "core/usuarios.html"
    return render(request, plantilla, contexto)


@login_required
@permission_required("core.add_usuario", raise_exception=True)
def usuario_crear(request):
    form = UsuarioCrearForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            usuario = form.save(commit=False)
            usuario.username = generar_usuario(usuario.first_name, usuario.last_name)
            clave = generar_codigo_acceso(usuario.first_name, usuario.last_name)
            usuario.set_password(clave)
            usuario.debe_cambiar_clave = False
            usuario.creado_por = request.user
            usuario.save()
            empleado = form.cleaned_data.get("empleado")
            if empleado:
                empleado.usuario = usuario
                empleado.save(update_fields=["usuario", "actualizado_en"])
        _guardar_credenciales(request, usuario, clave, nuevo=True)
        return redirect("usuario_credenciales")
    return render(request, "core/usuario_form.html", {"form": form})


@login_required
@permission_required("core.change_usuario", raise_exception=True)
@require_POST
def usuario_restablecer_clave(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    clave = generar_codigo_acceso(usuario.first_name, usuario.last_name)
    usuario.set_password(clave)
    usuario.debe_cambiar_clave = False
    usuario.save()
    _guardar_credenciales(request, usuario, clave, nuevo=False, volver=request.POST.get("volver"))
    return redirect("usuario_credenciales")


@login_required
@permission_required("core.change_usuario", raise_exception=True)
@require_POST
def usuario_cambiar_estado(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    if usuario == request.user:
        messages.error(request, "No puede desactivar su propia cuenta.")
    elif usuario.is_active and usuario.rol == Rol.ADMINISTRADOR and not Usuario.objects.filter(
        rol=Rol.ADMINISTRADOR, is_active=True).exclude(pk=usuario.pk).exists():
        messages.error(request, "Debe quedar al menos un administrador activo.")
    else:
        usuario.is_active = not usuario.is_active
        usuario.save(update_fields=["is_active"])
        estado = "activó" if usuario.is_active else "desactivó"
        messages.success(request, f"Se {estado} la cuenta {usuario.username} ({usuario.get_full_name()}).")
    return redirect("usuarios")


@login_required
@permission_required("core.add_usuario", raise_exception=True)
@never_cache
def usuario_credenciales(request):
    """Muestra el usuario y su código de acceso UNA sola vez; el código no se guarda en texto plano."""
    datos = request.session.pop(CLAVE_SESION_CREDENCIALES, None)
    if datos is None:
        return redirect("usuarios")
    return render(request, "core/usuario_credenciales.html", datos)


def _guardar_credenciales(request, usuario, clave, nuevo, volver=None):
    preparar_credenciales(request, usuario, clave, nuevo=nuevo, volver=volver)


# --------------------------------------------------------------------------- notificaciones
POR_PAGINA = 20


def _de(usuario):
    return Notificacion.objects.filter(destinatario=usuario)


@login_required
@require_POST
def marcar_notificaciones_leidas(request):
    _de(request.user).filter(leida=False).update(leida=True, leida_en=timezone.now())
    if _es_htmx(request):
        if request.GET.get("panel") == "campana":
            return _panel_campana(request)
        return render(request, "core/_notificaciones.html", {
            "notificaciones": _de(request.user).order_by("-creado_en")[:6],
            "notificaciones_sin_leer": 0, "actualizar_contador": True,
        })
    volver = request.POST.get("volver", "")
    return redirect(volver if url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}) else "notificaciones")


@login_required
def notificaciones(request):
    sin_leer = request.GET.get("ver") == "sin_leer"
    qs = _de(request.user).order_by("-creado_en")
    if sin_leer:
        qs = qs.filter(leida=False)
    return render(request, "core/notificaciones.html", {
        "pagina": Paginator(qs, POR_PAGINA).get_page(request.GET.get("pagina")), "sin_leer": sin_leer,
        "hoy": f"{timezone.localdate():%Y%m%d}",
    })


def _json(request) -> dict:
    try:
        datos = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return datos if isinstance(datos, dict) else {}


@login_required
@require_POST
def push_suscribir(request):
    """Guarda (o pasa a este usuario) la suscripción push del navegador."""
    datos = _json(request)
    endpoint, claves = str(datos.get("endpoint", "")), datos.get("keys") or {}
    p256dh, auth = str(claves.get("p256dh", "")), str(claves.get("auth", ""))
    if not endpoint.startswith("https://") or len(endpoint) > 2000 or not (0 < len(p256dh) <= 200 and 0 < len(auth) <= 100):
        return JsonResponse({"error": "Suscripción no válida."}, status=400)
    SuscripcionPush.objects.update_or_create(endpoint=endpoint, defaults={
        "usuario": request.user, "p256dh": p256dh, "auth": auth, "navegador": request.headers.get("User-Agent", "")[:255]})
    return JsonResponse({"ok": True})


@login_required
@require_POST
def push_desuscribir(request):
    SuscripcionPush.objects.filter(usuario=request.user, endpoint=str(_json(request).get("endpoint", ""))).delete()
    return JsonResponse({"ok": True})


@never_cache
def service_worker(request):
    """El service worker se sirve desde la raíz para poder recibir push en todo el sistema."""
    ruta = finders.find("js/sw.js")
    respuesta = HttpResponse(Path(ruta).read_bytes(), content_type="application/javascript; charset=utf-8")
    respuesta["Service-Worker-Allowed"] = "/"
    return respuesta


@login_required
@never_cache
def contadores(request):
    """
    HTMX cada 15 s (y al abrir cada página): campana, contadores del menú y avisos flotantes
    de las notificaciones nuevas, sin recargar la página (SCRUM-60).
    """
    from .context_processors import contadores as calcular

    avisos.generar_pendientes(request.user)
    return render(request, "core/_contadores.html", calcular(request.user) | {"avisos_nuevos": avisos.nuevas_para_mostrar(request)})


def _panel_campana(request):
    from .context_processors import contadores as calcular

    return render(request, "core/_campana.html", {
        "notificaciones": Notificacion.objects.filter(destinatario=request.user).order_by("leida", "-creado_en")[:8],
    } | calcular(request.user))


@login_required
@never_cache
def panel_notificaciones(request):
    """HTMX: lista de la campana (se carga al abrirla)."""
    return _panel_campana(request)


@login_required
def abrir_notificacion(request, pk):
    """Marca la notificación como leída y lleva a su página."""
    notificacion = get_object_or_404(Notificacion, pk=pk, destinatario=request.user)
    if not notificacion.leida:
        notificacion.leida, notificacion.leida_en = True, timezone.now()
        notificacion.save(update_fields=["leida", "leida_en"])
    destino = notificacion.url
    if not destino or not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        destino = reverse("inicio") + "#notificaciones"
    return redirect(destino)
