import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.core import validadores
from apps.core.cuentas import crear_cuenta_para_empleado, preparar_credenciales, rol_para_cargo
from apps.documentos import archivos
from apps.documentos.forms import GenerarContratoForm
from apps.documentos.models import Documento, TipoDocumento

from . import cv
from . import historial
from .forms import DocumentosIngresoForm, EmpleadoEditarForm, EmpleadoForm, EquipoForm, ObservacionForm, SalarioForm
from .models import AsignacionComision, Empleado, HistorialSalario

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
                existente = form.cleaned_data.get("cuenta_existente")
                if existente is not None:
                    # Cuenta creada antes desde Usuarios: se vincula (conserva su usuario y su código de acceso).
                    existente.rol = rol_para_cargo(self.object.cargo.nombre)
                    existente.save(update_fields=["rol"])
                    self.object.usuario = existente
                    self.object.save(update_fields=["usuario", "actualizado_en"])
                    usuario, clave = existente, None
                else:
                    usuario, clave = crear_cuenta_para_empleado(self.object, creado_por=self.request.user)
                configurar_ingreso(self.object, form.cleaned_data, self.request.user)
        except IntegrityError:
            # Otro usuario registró la misma identificación entre la validación y el guardado.
            self.object = None
            cuenta = form.cleaned_data.get("cuenta_existente")
            if cuenta is not None and Empleado.objects.filter(usuario=cuenta).exists():
                # La cuenta se vinculó a otro empleado entre la validación y el guardado (usuario es único).
                form.add_error("cuenta_existente", "Esa cuenta acaba de vincularse a otro empleado. Elija otra.")
            else:
                form.add_error("identificacion", "Ya existe un empleado registrado con esta identificación.")
            return self.render_to_response(self.get_context_data(form=form, documentos_form=documentos_form))

        messages.success(self.request, f"Empleado {self.object.nombres} {self.object.apellidos} registrado con sus documentos.")
        if clave is None:
            messages.info(self.request, f"Se vinculó la cuenta existente {usuario.username}: conserva su código de acceso.")
            return redirect("personal:empleado_detalle", pk=self.object.pk)
        preparar_credenciales(
            self.request, usuario, clave, nuevo=True,
            volver=reverse("personal:empleado_detalle", args=[self.object.pk]),
        )
        return redirect("usuario_credenciales")


class EmpleadoEditarView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """SCRUM-13 · Actualizar datos de empleados. Cada cambio queda en la auditoría con el usuario y el valor anterior."""

    permission_required = "personal.change_empleado"
    model = Empleado
    form_class = EmpleadoEditarForm
    template_name = "personal/empleado_form.html"

    def get_queryset(self):
        return Empleado.objects.select_related("cargo", "usuario")

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"editando": True}

    def form_valid(self, form):
        cambiados = set(form.changed_data)
        if not cambiados:
            messages.info(self.request, "No hubo cambios que guardar.")
            return redirect(self.get_success_url())
        try:
            with transaction.atomic():
                self.object = form.save()
                self._sincronizar_cuenta(cambiados)
        except IntegrityError:
            form.add_error("identificacion", "Ya existe un empleado registrado con esta identificación.")
            return self.form_invalid(form)
        nombres = ", ".join(str(form.fields[c].label).lower() for c in form.changed_data)
        messages.success(self.request, f"Datos actualizados: {nombres}.")
        return redirect(self.get_success_url())

    def _sincronizar_cuenta(self, cambiados):
        """La cuenta de acceso refleja el nombre, el rol del cargo y la desvinculación del empleado."""
        usuario = self.object.usuario
        if not usuario:
            return
        campos = []
        if cambiados & {"nombres", "apellidos"}:
            usuario.first_name, usuario.last_name = self.object.nombres, self.object.apellidos
            campos += ["first_name", "last_name"]
        if "cargo" in cambiados:
            usuario.rol = rol_para_cargo(self.object.cargo.nombre)
            campos.append("rol")
        if "estado" in cambiados and self.object.estado == Empleado.Estado.DESVINCULADO and usuario.is_active:
            usuario.is_active = False  # un empleado desvinculado ya no puede ingresar
            campos.append("is_active")
            messages.warning(self.request, f"Se desactivó la cuenta {usuario.username}: el empleado está desvinculado.")
        if campos:
            usuario.save()  # save() completo: dispara la sincronización del grupo de permisos por rol

    def get_success_url(self):
        return reverse("personal:empleado_detalle", args=[self.object.pk]) + "#datos"


def configurar_ingreso(empleado, datos, usuario):
    """Sueldo inicial (el indicado o el de la regla SUELDO_BASE), equipo de comisiones y horario estándar."""
    from apps.asistencia.models import AsignacionHorario, Horario
    from apps.nomina.calculo import sueldo_base_por_defecto

    HistorialSalario.objects.create(
        empleado=empleado, salario_base=datos.get("salario_base") or sueldo_base_por_defecto(empleado.fecha_ingreso),
        vigente_desde=empleado.fecha_ingreso, motivo="Sueldo inicial", registrado_por=usuario,
    )
    if datos.get("equipo"):
        AsignacionComision.objects.create(empleado=empleado, esquema=datos["equipo"], vigente_desde=empleado.fecha_ingreso)
    estandar = Horario.objects.filter(nombre="Jornada estándar", activo=True).first()
    if estandar and not AsignacionHorario.objects.filter(empleado=empleado).exists():
        AsignacionHorario.objects.create(empleado=empleado, horario=estandar, vigente_desde=empleado.fecha_ingreso)


class EmpleadoDetalleView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """
    Ficha del empleado: datos, documentos del expediente, cuenta de acceso y
    récord de asistencia (faltas, atrasos y cuántos fueron justificados).
    """

    permission_required = "personal.view_empleado"
    model = Empleado
    template_name = "personal/empleado_detalle.html"
    context_object_name = "empleado"

    PERIODOS = [("mes", "Este mes"), ("anio", "Este año"), ("todo", "Desde el ingreso")]

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

        if self.request.user.has_perm("asistencia.view_novedadasistencia"):
            from apps.asistencia.servicios import horario_de, resumen_empleado

            hoy = timezone.localdate()
            periodo = self.request.GET.get("periodo", "todo")
            if periodo not in dict(self.PERIODOS):
                periodo = "todo"
            desde = {"mes": hoy.replace(day=1), "anio": hoy.replace(month=1, day=1)}.get(periodo)
            ctx.update({
                "asistencia": resumen_empleado(self.object, desde, hoy),
                "periodo": periodo,
                "periodos": self.PERIODOS,
                "horario_actual": horario_de(self.object, hoy),
            })

        if self.request.user.has_perm("core.view_registrocambio"):
            ctx["historial_cambios"] = historial.cambios_de(self.object)

        if self.request.user.has_perm("personal.view_historialsalario"):
            from apps.nomina.calculo import estimar_mes

            hoy = timezone.localdate()
            ctx.update({
                "salarios": self.object.salarios.select_related("registrado_por").order_by("-vigente_desde"),
                "equipos": self.object.comisiones.select_related("esquema").order_by("-vigente_desde"),
                "estimacion": estimar_mes(self.object, hoy.year, hoy.month),
            })
            if self.request.user.has_perm("personal.add_historialsalario"):
                ctx["salario_form"] = SalarioForm(empleado=self.object)
                ctx["equipo_form"] = EquipoForm(empleado=self.object)

        ctx["observaciones"] = self.object.observaciones.select_related("autor")
        ctx["observacion_form"] = ObservacionForm()

        ctx["contratos"] = self.object.contratos.select_related("plantilla", "documento", "generado_por").order_by("-creado_en")
        if self.request.user.has_perm("documentos.add_contratogenerado"):
            ctx["contrato_form"] = GenerarContratoForm(empleado=self.object)
        return ctx


@login_required
@permission_required("personal.add_observacionempleado", raise_exception=True)
@require_POST
def observacion_crear(request, pk):
    empleado = get_object_or_404(Empleado, pk=pk)
    form = ObservacionForm(request.POST)
    if form.is_valid():
        observacion = form.save(commit=False)
        observacion.empleado = empleado
        observacion.autor = request.user
        observacion.save()
        messages.success(request, "Observación registrada.")
    else:
        messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect(reverse("personal:empleado_detalle", args=[pk]) + "#observaciones")


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
def verificar_identificacion(request):
    """HTMX: avisa mientras se escribe si la identificación ya está registrada o no es válida."""
    if not (request.user.has_perm("personal.add_empleado") or request.user.has_perm("personal.change_empleado")):
        raise PermissionDenied
    excluir = request.GET.get("excluir", "")
    tipo = request.GET.get("tipo_identificacion", "")
    identificacion = validadores.normalizar_identificacion(request.GET.get("identificacion", ""))
    contexto = {"estado": ""}
    if identificacion:
        try:
            validadores.validar_identificacion(tipo, identificacion)
        except ValidationError as error:
            contexto = {"estado": "invalida", "mensaje": " ".join(error.messages)}
        else:
            existente = Empleado.objects.filter(tipo_identificacion=tipo, identificacion=identificacion)
            if excluir.isdigit():
                existente = existente.exclude(pk=int(excluir))
            existente = existente.first()
            contexto = {"estado": "duplicada", "empleado": existente} if existente else {"estado": "disponible"}
    return render(request, "personal/_identificacion_estado.html", contexto)


# --------------------------------------------------------------------------- SCRUM-14 · salarios y comisiones
def _volver_a_sueldo(pk):
    return redirect(reverse("personal:empleado_detalle", args=[pk]) + "#sueldo")


def _cerrar_vigente(modelo, empleado, desde):
    """El registro vigente termina el día anterior al nuevo: el historial no tiene huecos ni solapes."""
    modelo.objects.filter(empleado=empleado, vigente_hasta__isnull=True).update(vigente_hasta=desde - timedelta(days=1))


@login_required
@permission_required("personal.add_historialsalario", raise_exception=True)
@require_POST
def salario_cambiar(request, pk):
    empleado = get_object_or_404(Empleado, pk=pk)
    form = SalarioForm(request.POST, empleado=empleado)
    if not form.is_valid():
        messages.error(request, "No se cambió el sueldo: " + " ".join(e for lista in form.errors.values() for e in lista))
        return _volver_a_sueldo(pk)
    with transaction.atomic():
        desde = form.cleaned_data["vigente_desde"]
        _cerrar_vigente(HistorialSalario, empleado, desde)
        nuevo = HistorialSalario.objects.create(
            empleado=empleado, salario_base=form.cleaned_data["salario_base"], vigente_desde=desde,
            motivo=form.cleaned_data["motivo"], registrado_por=request.user,
        )
    messages.success(request, f"Sueldo base actualizado a $ {nuevo.salario_base} desde el {desde:%d/%m/%Y}.")
    return _volver_a_sueldo(pk)


@login_required
@permission_required("personal.add_asignacioncomision", raise_exception=True)
@require_POST
def equipo_cambiar(request, pk):
    empleado = get_object_or_404(Empleado, pk=pk)
    form = EquipoForm(request.POST, empleado=empleado)
    if not form.is_valid():
        messages.error(request, "No se cambió el equipo: " + " ".join(e for lista in form.errors.values() for e in lista))
        return _volver_a_sueldo(pk)
    with transaction.atomic():
        desde = form.cleaned_data["vigente_desde"]
        _cerrar_vigente(AsignacionComision, empleado, desde)
        AsignacionComision.objects.create(empleado=empleado, esquema=form.cleaned_data["esquema"], vigente_desde=desde)
    messages.success(request, f"Equipo cambiado a {form.cleaned_data['esquema']} desde el {desde:%d/%m/%Y}.")
    return _volver_a_sueldo(pk)
