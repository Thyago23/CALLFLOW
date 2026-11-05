from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView

from apps.core import validadores

from .forms import EmpleadoForm
from .models import Empleado


def es_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


class EmpleadoListaView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """Listado del personal, con búsqueda en vivo (HTMX) y filtro por estado."""

    permission_required = "personal.view_empleado"
    model = Empleado
    paginate_by = 20
    template_name = "personal/empleado_lista.html"
    context_object_name = "empleados"

    def get_queryset(self):
        qs = Empleado.objects.select_related("cargo").order_by("apellidos", "nombres")
        texto = self.request.GET.get("q", "").strip()
        if texto:
            identificacion = validadores.normalizar_identificacion(texto)
            filtro = Q(identificacion__icontains=identificacion)
            for palabra in texto.split():
                filtro |= Q(nombres__icontains=palabra) | Q(apellidos__icontains=palabra)
            qs = qs.filter(filtro)
        estado = self.request.GET.get("estado", "")
        if estado in Empleado.Estado.values:
            qs = qs.filter(estado=estado)
        return qs

    def get_template_names(self):
        return ["personal/_empleado_tabla.html"] if es_htmx(self.request) else [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["estado"] = self.request.GET.get("estado", "")
        ctx["estados"] = Empleado.Estado.choices
        ctx["resaltado"] = self.request.GET.get("nuevo", "")
        return ctx


class EmpleadoDetalleView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """Ficha del empleado con su récord de asistencia (faltas, atrasos y justificaciones)."""

    permission_required = "personal.view_empleado"
    model = Empleado
    template_name = "personal/empleado_detalle.html"
    context_object_name = "empleado"

    PERIODOS = [("mes", "Este mes"), ("anio", "Este año"), ("todo", "Desde el ingreso")]

    def get_queryset(self):
        return Empleado.objects.select_related("cargo", "usuario")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.has_perm("asistencia.view_novedadasistencia"):
            from apps.asistencia.servicios import horario_de, resumen_empleado

            hoy = timezone.localdate()
            periodo = self.request.GET.get("periodo", "todo")
            desde = {"mes": hoy.replace(day=1), "anio": hoy.replace(month=1, day=1)}.get(periodo)
            if periodo not in dict(self.PERIODOS):
                periodo = "todo"
            ctx.update({
                "asistencia": resumen_empleado(self.object, desde, hoy),
                "periodo": periodo,
                "periodos": self.PERIODOS,
                "horario_actual": horario_de(self.object, hoy),
            })
        return ctx


class EmpleadoCrearView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """SCRUM-12 · Registrar nuevos empleados."""

    permission_required = "personal.add_empleado"
    model = Empleado
    form_class = EmpleadoForm
    template_name = "personal/empleado_form.html"

    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.object = form.save()
        except IntegrityError:
            # Otro usuario registró la misma identificación entre la validación y el guardado.
            form.add_error("identificacion", "Ya existe un empleado registrado con esta identificación.")
            return self.form_invalid(form)
        messages.success(self.request, f"Empleado {self.object.nombres} {self.object.apellidos} registrado correctamente.")
        return super().form_valid(form)

    def get_success_url(self):
        return f"{reverse('personal:empleado_lista')}?nuevo={self.object.pk}"


@login_required
@permission_required("personal.add_empleado", raise_exception=True)
def verificar_identificacion(request):
    """HTMX: avisa mientras se escribe si la identificación ya está registrada o no es válida."""
    tipo = request.GET.get("tipo_identificacion", "")
    identificacion = validadores.normalizar_identificacion(request.GET.get("identificacion", ""))
    contexto = {"estado": ""}
    if identificacion:
        try:
            validadores.validar_identificacion(tipo, identificacion)
        except ValidationError as error:
            contexto = {"estado": "invalida", "mensaje": " ".join(error.messages)}
        else:
            existente = Empleado.objects.filter(tipo_identificacion=tipo, identificacion=identificacion).first()
            contexto = {"estado": "duplicada", "empleado": existente} if existente else {"estado": "disponible"}
    return render(request, "personal/_identificacion_estado.html", contexto)
