from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.agenda.models import Evento
from apps.inventario.models import Material
from apps.personal.models import Empleado

from .codigos import generar_codigo_acceso, generar_usuario
from .cuentas import CLAVE_SESION_CREDENCIALES, preparar_credenciales
from .forms import CambioClaveForm, IngresoForm, UsuarioCrearForm
from .modulos import SECCIONES, modulos_de
from .models import Notificacion, Rol, Usuario


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
    visibles = modulos_de(usuario)

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
            "url": reverse("asistencia:novedades"), "alerta": True,
        })
    if usuario.has_perm("inventario.view_material"):
        indicadores.append({
            "titulo": "Materiales con stock bajo", "icono": "box-seam",
            "valor": Material.objects.filter(activo=True, stock_actual__lte=F("stock_minimo")).count(),
            "alerta": True,
        })
    if usuario.has_perm("agenda.view_evento"):
        indicadores.append({
            "titulo": "Mis actividades de hoy", "icono": "calendar3",
            "valor": Evento.objects.filter(propietario=usuario, estado=Evento.Estado.PENDIENTE, inicio__date=hoy).count(),
        })

    secciones = [
        {"codigo": codigo, "nombre": nombre, "modulos": [m for m in visibles if m.seccion == codigo]}
        for codigo, nombre in SECCIONES
    ]
    hora = timezone.localtime().hour
    return render(request, "core/dashboard.html", {
        "saludo": "Buenos días" if hora < 12 else "Buenas tardes" if hora < 19 else "Buenas noches",
        "indicadores": indicadores,
        "secciones": [s for s in secciones if s["modulos"]],
        "notificaciones": Notificacion.objects.filter(destinatario=usuario).order_by("leida", "-creado_en")[:6],
        "modulos_listos": sum(m.disponible for m in visibles),
        "modulos_total": len(visibles),
    })


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
@login_required
@require_POST
def marcar_notificaciones_leidas(request):
    Notificacion.objects.filter(destinatario=request.user, leida=False).update(leida=True, leida_en=timezone.now())
    if _es_htmx(request):
        notificaciones = Notificacion.objects.filter(destinatario=request.user).order_by("-creado_en")[:6]
        return render(request, "core/_notificaciones.html", {"notificaciones": notificaciones})
    return redirect("inicio")
