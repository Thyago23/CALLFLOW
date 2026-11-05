import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView

from apps.core import validadores
from apps.core.cuentas import crear_cuenta_para_empleado, preparar_credenciales
from apps.documentos import archivos
from apps.documentos.models import Documento, TipoDocumento

from . import cv
from .forms import DocumentosIngresoForm, EmpleadoForm
from .models import Empleado

# Campos que el lector de CV puede llenar, con el nombre que ve el usuario.
CAMPOS_CV = (
    ("identificacion", "cédula"), ("nombres", "nombres"), ("apellidos", "apellidos"),
    ("fecha_nacimiento", "fecha de nacimiento"), ("telefono", "teléfono"), ("email", "correo"),
    ("direccion", "dirección"),
)


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


class EmpleadoCrearView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    SCRUM-12 · Registrar nuevos empleados, con su expediente de ingreso (CV, cédula y título)
    y su cuenta de acceso (usuario nombre.apellido + código de acceso), creada automáticamente.
    """

    permission_required = ("personal.add_empleado", "documentos.add_documento", "core.add_usuario")
    model = Empleado
    form_class = EmpleadoForm
    template_name = "personal/empleado_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("documentos_form", DocumentosIngresoForm())
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = None
        form = self.get_form()
        documentos_form = DocumentosIngresoForm(request.POST, request.FILES)
        empleado_valido = form.is_valid()
        documentos_validos = documentos_form.is_valid()
        if empleado_valido and documentos_validos:
            return self.form_valid(form, documentos_form)
        return self.render_to_response(self.get_context_data(form=form, documentos_form=documentos_form))

    def form_valid(self, form, documentos_form):
        try:
            with transaction.atomic():
                self.object = form.save()
                for campo, codigo in DocumentosIngresoForm.TIPOS.items():
                    archivos.guardar_documento(
                        empleado=self.object, codigo_tipo=codigo,
                        archivo=documentos_form.cleaned_data[campo], usuario=self.request.user,
                    )
                usuario, clave = crear_cuenta_para_empleado(self.object, creado_por=self.request.user)
        except IntegrityError:
            # Otro usuario registró la misma identificación entre la validación y el guardado.
            self.object = None
            form.add_error("identificacion", "Ya existe un empleado registrado con esta identificación.")
            return self.render_to_response(self.get_context_data(form=form, documentos_form=documentos_form))

        messages.success(self.request, f"Empleado {self.object.nombres} {self.object.apellidos} registrado con sus documentos.")
        preparar_credenciales(
            self.request, usuario, clave, nuevo=True,
            volver=reverse("personal:empleado_detalle", args=[self.object.pk]),
        )
        return redirect("usuario_credenciales")


class EmpleadoDetalleView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """Ficha del empleado: datos, documentos del expediente y cuenta de acceso."""

    permission_required = "personal.view_empleado"
    model = Empleado
    template_name = "personal/empleado_detalle.html"
    context_object_name = "empleado"

    def get_queryset(self):
        return Empleado.objects.select_related("cargo", "usuario")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        documentos = list(
            self.object.documentos.filter(eliminado=False).select_related("tipo").order_by("tipo__nombre", "-creado_en")
        )
        ctx["documentos"] = documentos
        ctx["documentos_faltantes"] = TipoDocumento.objects.filter(requerido_ingreso=True, activo=True).exclude(
            codigo__in={d.tipo.codigo for d in documentos}
        )
        return ctx


@login_required
@permission_required("personal.add_empleado", raise_exception=True)
@require_POST
def leer_cv(request):
    """HTMX: lee el CV adjuntado y devuelve los datos encontrados para prellenar el formulario."""
    archivo = request.FILES.get("hoja_vida")
    datos = {}
    if archivo is None:
        contexto = {"estado": "error", "mensaje": "No se recibió el archivo."}
    else:
        try:
            mime = archivos.validar_archivo(archivo)
        except ValidationError as error:
            contexto = {"estado": "error", "mensaje": " ".join(error.messages)}
        else:
            if mime != "application/pdf":
                contexto = {"estado": "aviso", "mensaje": "El CV es una imagen: se guardará, pero sus datos no se pueden leer. Llene el formulario a mano."}
            else:
                try:
                    datos = cv.leer_cv(archivo)
                except cv.CVIlegible:
                    contexto = {"estado": "aviso", "mensaje": "El PDF no tiene texto legible (parece escaneado). Llene el formulario a mano."}
                else:
                    contexto = {
                        "estado": "ok" if datos else "aviso",
                        "encontrados": [etiqueta for campo, etiqueta in CAMPOS_CV if campo in datos],
                        "faltantes": [etiqueta for campo, etiqueta in CAMPOS_CV if campo not in datos],
                        "mensaje": "" if datos else "No se encontraron datos reconocibles en el CV. Llene el formulario a mano.",
                    }
    respuesta = render(request, "personal/_cv_estado.html", contexto)
    if datos:
        # json.dumps escapa las tildes (\uXXXX): la cabecera HTTP queda en ASCII.
        respuesta["HX-Trigger"] = json.dumps({"cvLeido": {"campos": datos}})
    return respuesta


@login_required
@permission_required("documentos.view_documento", raise_exception=True)
def documento_ver(request, pk):
    """Entrega el archivo solo a usuarios con permiso: los adjuntos no son públicos."""
    documento = get_object_or_404(Documento, pk=pk, eliminado=False, empleado__isnull=False)
    respuesta = FileResponse(documento.archivo.open("rb"), content_type=documento.tipo_mime)
    respuesta["Content-Disposition"] = content_disposition_header(False, documento.nombre_original)
    return respuesta


@login_required
@permission_required("core.add_usuario", raise_exception=True)
@require_POST
def empleado_crear_cuenta(request, pk):
    """Para empleados registrados antes de que la cuenta se creara automáticamente."""
    with transaction.atomic():
        empleado = get_object_or_404(Empleado.objects.select_for_update().select_related("cargo"), pk=pk)
        if empleado.usuario_id:
            messages.info(request, "Este empleado ya tiene una cuenta de acceso.")
            return redirect("personal:empleado_detalle", pk=pk)
        usuario, clave = crear_cuenta_para_empleado(empleado, creado_por=request.user)
    preparar_credenciales(request, usuario, clave, nuevo=True, volver=reverse("personal:empleado_detalle", args=[pk]))
    return redirect("usuario_credenciales")


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
