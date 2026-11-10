"""4.5 Reportes y exportación: centro de reportes, vista previa, PDF y estadísticas (SCRUM-61)."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header

from apps.nomina.calculo import nombre_mes

from . import estadisticas as stats
from . import graficas
from .catalogo import ESTADISTICAS, REPORTES, Columna, Tabla
from .filtros import FiltrosForm
from .formato import dinero
from .models import ReporteGenerado, TipoReporte
from .pdf import estadisticas_pdf

VER = "reportes.view_tiporeporte"
ICONOS_MODULO = {"ADMINISTRATIVO": "building", "NOMINA": "cash-coin", "VENTAS": "telephone-outbound", "CIERRE": "check2-circle"}


def puede(usuario, tipo: TipoReporte) -> bool:
    return usuario.is_superuser or usuario.rol in tipo.roles_permitidos


def _tipo(usuario, codigo: str):
    codigo = codigo.upper()
    definicion = REPORTES.get(codigo) or (ESTADISTICAS if codigo == ESTADISTICAS.codigo else None)
    tipo = TipoReporte.objects.filter(codigo=codigo, activo=True).first()
    if definicion is None or tipo is None:
        raise Http404("Ese reporte no existe.")
    if not puede(usuario, tipo):
        raise PermissionDenied("Su rol no puede generar este reporte.")
    return definicion, tipo


def _formulario(request, definicion):
    """Filtros con sus valores por defecto (mes en curso, del 1 del mes a hoy) más lo que eligió el usuario."""
    hoy = timezone.localdate()
    defecto = {"desde": hoy.replace(day=1).isoformat(), "hasta": hoy.isoformat(), "mes": f"{hoy:%Y-%m}"}
    datos = {k: v for k, v in defecto.items() if k in definicion.filtros} | request.GET.dict()
    return FiltrosForm(datos, definicion, request.user)


def _registrar(tipo, request, form, contenido: bytes):
    ReporteGenerado.objects.create(tipo=tipo, generado_por=request.user, filtros=form.serializado(),
                                   formato=ReporteGenerado.Formato.PDF, tamano_bytes=len(contenido))


def _respuesta_pdf(contenido: bytes, nombre: str) -> HttpResponse:
    respuesta = HttpResponse(contenido, content_type="application/pdf")
    respuesta["Content-Disposition"] = content_disposition_header(False, f"{nombre} {timezone.localdate():%Y-%m-%d}.pdf")
    respuesta["X-Content-Type-Options"] = "nosniff"
    return respuesta


# --------------------------------------------------------------------------- centro de reportes (SCRUM-62)
@login_required
@permission_required(VER, raise_exception=True)
def centro(request):
    tipos = [t for t in TipoReporte.objects.filter(activo=True) if puede(request.user, t)]
    grupos = []
    for modulo, etiqueta in TipoReporte.Modulo.choices:
        reportes = [REPORTES[t.codigo] for t in tipos if t.modulo == modulo and t.codigo in REPORTES]
        if reportes:
            grupos.append({"nombre": etiqueta, "icono": ICONOS_MODULO[modulo], "reportes": reportes})
    return render(request, "reportes/centro.html", {
        "grupos": grupos,
        "estadisticas": any(t.codigo == ESTADISTICAS.codigo for t in tipos),
        "recientes": ReporteGenerado.objects.filter(generado_por=request.user).select_related("tipo")[:6],
    })


@login_required
@permission_required(VER, raise_exception=True)
def reporte(request, codigo):
    definicion, _ = _tipo(request.user, codigo)
    if definicion is ESTADISTICAS:
        return redirect("reportes:estadisticas")
    form = _formulario(request, definicion)
    faltan = [form.fields[f].label for f in definicion.obligatorios if f in form.fields and not request.GET.get(f)]
    tabla = definicion.tabla(form.valores(), request.user) if not faltan and form.is_valid() else None
    return render(request, "reportes/reporte.html", {
        "r": definicion, "form": form, "tabla": tabla, "faltan": faltan, "mostrar_errores": bool(request.GET),
        "consulta": request.GET.urlencode(),
    })


@login_required
@permission_required(VER, raise_exception=True)
def reporte_pdf(request, codigo):
    definicion, tipo = _tipo(request.user, codigo)
    form = _formulario(request, definicion)
    if not form.is_valid():
        messages.error(request, "Revise los filtros antes de descargar el PDF.")
        return redirect(f"{reverse('reportes:reporte', args=[definicion.codigo.lower()])}?{request.GET.urlencode()}")
    contenido = definicion.pdf(form.valores(), request.user, form.texto())
    _registrar(tipo, request, form, contenido)
    return _respuesta_pdf(contenido, definicion.nombre)


# --------------------------------------------------------------------------- estadísticas (SCRUM-67 a 69)
def _datos_grafica(est):
    """(etiquetas, reales, proyectados) de la gráfica mensual: 12 meses reales y, si es el mes en curso, 3 proyectados."""
    etiquetas = [m.etiqueta for m in est.serie] + [p["etiqueta"] for p in est.proximos]
    reales = [m.total for m in est.serie] + [None] * len(est.proximos)
    proyectados = [None] * len(etiquetas)
    if est.en_curso and est.proyeccion_mes is not None:
        n = len(est.serie)
        proyectados[n - 2] = est.serie[-2].total  # la punteada sale del último mes completo
        proyectados[n - 1] = est.proyeccion_mes
        for i, p in enumerate(est.proximos):
            proyectados[n + i] = p["tendencia"]
    return etiquetas, reales, proyectados


def _tablas(est):
    tablas = []
    if est.en_curso:
        filas = [[est.actual.etiqueta + " (en curso)", est.proyeccion_mes, est.meta,
                  f"Ritmo diario: {est.transcurridos} de {est.laborables} días laborables"]]
        metodo = (f"Tendencia lineal de {est.meses_base} mes{'es' if est.meses_base != 1 else ''}" if est.meses_base
                  else "Sin meses completos: aún no se puede calcular")
        filas += [[p["etiqueta"], p["tendencia"], None, metodo] for p in est.proximos]
        notas = [f"Promedio de los últimos 3 meses completos: {dinero(est.promedio_3)}."] if est.promedio_3 is not None else []
        if not est.confiable:
            notas.append("Hay menos de 3 meses completos de historia: la tendencia es poco confiable.")
        tablas.append(("Proyección", Tabla(
            columnas=[Columna("Mes", peso=1.4), Columna("Proyección", "dinero"), Columna("Meta", "dinero"), Columna("Método", peso=2.6)],
            filas=filas, notas=notas)))
    columnas = [Columna("N.°", "entero", .5), Columna("Vendedor", peso=2.4), Columna("Cerradas", "entero", .8),
                Columna("Total vendido", "dinero"), Columna("Meta", "dinero")]
    if est.en_curso:
        columnas.append(Columna("Proyección", "dinero"))
    filas = [[i, f"{r['vendedor'].apellidos} {r['vendedor'].nombres}", r["cerradas"], r["total"], r["meta"]]
             + ([r.get("proyeccion")] if est.en_curso else []) for i, r in enumerate(est.ranking, start=1)]
    tablas.append(("Ranking de vendedores", Tabla(columnas=columnas, filas=filas, vacio="No hay ventas en este mes.")))
    tablas.append(("Ventas por equipo", Tabla(columnas=[Columna("Equipo", peso=3), Columna("Total vendido", "dinero")],
                                              filas=[[e["equipo"], e["total"]] for e in est.equipos],
                                              vacio="No hay ventas en este mes.")))
    return tablas


def _barras(filas, clave, etiqueta):
    """Filas para las barras horizontales en HTML, con el ancho relativo al mayor."""
    maximo = max((Decimal(f[clave]) for f in filas), default=Decimal("0"))
    return [{"etiqueta": etiqueta(f), "valor": f[clave], "ancho": round(Decimal(f[clave]) * 100 / maximo) if maximo else 0}
            for f in filas]


def _estadisticas(request):
    _, tipo = _tipo(request.user, ESTADISTICAS.codigo)
    form = _formulario(request, ESTADISTICAS)
    if not form.is_valid():
        form = FiltrosForm({"mes": f"{timezone.localdate():%Y-%m}"}, ESTADISTICAS, request.user)
        form.is_valid()
    f = form.valores()
    est = stats.calcular(*f["mes"], equipo=f.get("equipo"), vendedor=f.get("vendedor"))
    return est, form, tipo


@login_required
@permission_required(VER, raise_exception=True)
def estadisticas(request):
    est, form, _ = _estadisticas(request)
    etiquetas, reales, proyectados = _datos_grafica(est)
    a = est.actual
    return render(request, "reportes/estadisticas.html", {
        "est": est, "form": form, "consulta": request.GET.urlencode(), "nombre_mes": nombre_mes(est.anio, est.mes),
        "grafica_mensual": graficas.linea_mensual(etiquetas, reales, proyectados),
        "grafica_diaria": graficas.acumulado_mes(stats.ultimo_dia(est.anio, est.mes).day, est.diario, est.proyeccion_mes, est.meta)
        if est.diario else None,
        "serie_tabla": list(zip(etiquetas, reales, proyectados)),
        "ranking": _barras(est.ranking[:10], "total", lambda r: f"{r['vendedor'].apellidos} {r['vendedor'].nombres}"),
        "equipos": _barras(est.equipos, "total", lambda e: e["equipo"]),
        "embudo": _barras([{"n": n, "t": t} for t, n in est.embudo], "n", lambda e: e["t"]),
        "tablas": _tablas(est),
        "hay_ventas": any(m.enviados for m in est.serie) or a.enviados,
    })


@login_required
@permission_required(VER, raise_exception=True)
def estadisticas_descarga(request):
    est, form, tipo = _estadisticas(request)
    contenido = estadisticas_pdf(est, _datos_grafica(est), _tablas(est), form.texto(), request.user)
    _registrar(tipo, request, form, contenido)
    return _respuesta_pdf(contenido, ESTADISTICAS.nombre)
