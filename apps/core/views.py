from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.db import connection, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.agenda.models import Evento
from apps.inventario.models import Material
from apps.personal.models import Empleado

from .forms import AprobacionForm, IngresoForm, RegistroForm
from .modulos import SECCIONES, modulos_de
from .models import Notificacion, Rol, Usuario


def _pendientes():
    return Usuario.objects.filter(is_active=False, aprobado_en__isnull=True).order_by("date_joined")


def _es_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


# --------------------------------------------------------------------------- acceso
class IngresoView(auth_views.LoginView):
    form_class = IngresoForm
    redirect_authenticated_user = True


def registro(request):
    if request.user.is_authenticated:
        return redirect("inicio")

    form = RegistroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Bloqueo para que dos registros simultáneos no se conviertan ambos en el primer administrador.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext('callflow_registro'))")
            hay_admin = Usuario.objects.filter(rol=Rol.ADMINISTRADOR, is_active=True).exists()

            usuario = form.save(commit=False)
            if hay_admin:
                usuario.is_active = False  # queda pendiente de aprobación
            else:
                usuario.rol = Rol.ADMINISTRADOR
                usuario.is_active = True
                usuario.aprobado_en = timezone.now()
            usuario.save()

            if hay_admin:
                Notificacion.objects.bulk_create(
                    Notificacion(
                        destinatario=admin, tipo=Notificacion.Tipo.GENERAL,
                        titulo=f"Nueva solicitud de acceso: {usuario.get_full_name()}",
                        mensaje=f"Solicita el rol {usuario.get_rol_display()}. Usuario: {usuario.username}.",
                        url=reverse("solicitudes_acceso"),
                    )
                    for admin in Usuario.objects.filter(rol=Rol.ADMINISTRADOR, is_active=True)
                )

        if hay_admin:
            return render(request, "registration/registro_enviado.html", {"usuario": usuario})
        messages.success(request, "Cuenta creada. Como es la primera cuenta del sistema, quedó como Administrador. Ya puede ingresar.")
        return redirect("login")

    return render(request, "registration/registro.html", {"form": form})


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
    if usuario.has_perm("core.change_usuario"):
        indicadores.append({
            "titulo": "Solicitudes de acceso", "icono": "person-plus",
            "valor": _pendientes().count(), "url": reverse("solicitudes_acceso"), "alerta": True,
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
            "valor": Evento.objects.filter(
                propietario=usuario, estado=Evento.Estado.PENDIENTE, inicio__date=hoy).count(),
        })

    secciones = [
        {"codigo": codigo, "nombre": nombre, "modulos": [m for m in visibles if m.seccion == codigo]}
        for codigo, nombre in SECCIONES
    ]
    hora = timezone.localtime().hour
    contexto = {
        "saludo": "Buenos días" if hora < 12 else "Buenas tardes" if hora < 19 else "Buenas noches",
        "indicadores": indicadores,
        "secciones": [s for s in secciones if s["modulos"]],
        "notificaciones": Notificacion.objects.filter(destinatario=usuario).order_by("leida", "-creado_en")[:6],
        "modulos_listos": sum(m.disponible for m in visibles),
        "modulos_total": len(visibles),
    }
    if usuario.has_perm("core.change_usuario"):
        contexto["pendientes"] = _pendientes()[:5]
        contexto["roles"] = Rol.choices
    return render(request, "core/dashboard.html", contexto)


# --------------------------------------------------------------------------- solicitudes de acceso
@login_required
@permission_required("core.change_usuario", raise_exception=True)
def solicitudes_acceso(request):
    return render(request, "core/solicitudes.html", {"pendientes": _pendientes(), "roles": Rol.choices})


@login_required
@permission_required("core.change_usuario", raise_exception=True)
@require_POST
def aprobar_solicitud(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk, is_active=False, aprobado_en__isnull=True)
    form = AprobacionForm(request.POST)
    if form.is_valid():
        usuario.rol = form.cleaned_data["rol"]
        usuario.is_active = True
        usuario.aprobado_en = timezone.now()
        usuario.aprobado_por = request.user
        usuario.save()
        Notificacion.objects.create(
            destinatario=usuario, titulo="¡Bienvenido a CALLFLOW!",
            mensaje=f"Su cuenta fue aprobada con el rol {usuario.get_rol_display()}.",
        )
        texto = f"Se aprobó la cuenta de {usuario.get_full_name()} como {usuario.get_rol_display()}."
    else:
        texto = "Seleccione un rol válido."
    return _respuesta_solicitud(request, texto, aprobada=form.is_valid())


@login_required
@permission_required("core.change_usuario", raise_exception=True)
@require_POST
def rechazar_solicitud(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk, is_active=False, aprobado_en__isnull=True)
    nombre = usuario.get_full_name() or usuario.username
    usuario.delete()  # nunca tuvo acceso; el borrado queda en la bitácora de auditoría
    return _respuesta_solicitud(request, f"Se rechazó la solicitud de {nombre}.", aprobada=False)


def _respuesta_solicitud(request, texto, aprobada):
    if _es_htmx(request):
        return render(request, "core/_solicitud_resuelta.html", {"texto": texto, "aprobada": aprobada})
    messages.success(request, texto) if aprobada else messages.info(request, texto)
    return redirect(request.POST.get("volver") or "solicitudes_acceso")


# --------------------------------------------------------------------------- notificaciones
@login_required
@require_POST
def marcar_notificaciones_leidas(request):
    Notificacion.objects.filter(destinatario=request.user, leida=False).update(leida=True, leida_en=timezone.now())
    if _es_htmx(request):
        notificaciones = Notificacion.objects.filter(destinatario=request.user).order_by("-creado_en")[:6]
        return render(request, "core/_notificaciones.html", {"notificaciones": notificaciones})
    return redirect("inicio")
