"""
Gráficas en SVG generadas en el servidor (sin librerías externas ni conexión a internet).

Un solo tono (el azul de la marca): lo real en línea continua y lo proyectado en línea
punteada; la meta, en gris punteado. Eje único, cuadrícula de línea fina, etiquetas solo en
los extremos y un tooltip (<title>) en cada punto. Cada gráfica tiene su tabla de datos en
la página, así que ningún valor depende solo del color o del tooltip.
"""
from math import floor, log10

from django.utils.html import escape
from django.utils.safestring import mark_safe

from .formato import compacto, dinero

AZUL, GRIS_META, REJILLA, TEXTO = "#0e325c", "#8a94a3", "#e4e8ee", "#5f6b7a"
ANCHO, ALTO = 760, 280
M_IZQ, M_DER, M_SUP, M_INF = 58, 24, 22, 34


def tope(valor) -> float:
    """Máximo «redondo» del eje: 830 -> 1000, 2300 -> 2500."""
    valor = float(valor or 0)
    if valor <= 0:
        return 100.0
    escala = 10 ** floor(log10(valor))
    return next(m * escala for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if valor <= m * escala)


def _ejes(maximo, etiquetas_x, posiciones_x, alto_plot):
    partes = []
    for i in range(5):
        v = maximo * i / 4
        y = M_SUP + alto_plot * (1 - i / 4)
        partes.append(f'<line x1="{M_IZQ}" x2="{ANCHO - M_DER}" y1="{y:.1f}" y2="{y:.1f}" stroke="{REJILLA}" stroke-width="1"/>')
        partes.append(f'<text x="{M_IZQ - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="{TEXTO}">{escape(compacto(v))}</text>')
    for etiqueta, x in zip(etiquetas_x, posiciones_x):
        if etiqueta:
            partes.append(f'<text x="{x:.1f}" y="{ALTO - 10}" text-anchor="middle" font-size="11" fill="{TEXTO}">{escape(etiqueta)}</text>')
    return partes


def _punto(x, y, titulo, hueco=False):
    relleno = "#fff" if hueco else AZUL
    return (f'<g><title>{escape(titulo)}</title><circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="transparent"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{relleno}" stroke="{AZUL if hueco else "#fff"}" stroke-width="2"/></g>')


def _etiqueta(x, y, texto, ancla="middle"):
    return f'<text x="{x:.1f}" y="{y - 10:.1f}" text-anchor="{ancla}" font-size="12" font-weight="600" fill="#1d2733">{escape(texto)}</text>'


def _svg(titulo, descripcion, partes):
    return mark_safe(
        f'<svg viewBox="0 0 {ANCHO} {ALTO}" class="grafica" role="img" aria-label="{escape(titulo)}" '
        f'font-family="inherit" xmlns="http://www.w3.org/2000/svg"><desc>{escape(descripcion)}</desc>{"".join(partes)}</svg>'
    )


def linea_mensual(etiquetas, reales, proyectados) -> str:
    """
    `reales` y `proyectados` tienen una posición por etiqueta (None donde no hay dato).
    La línea punteada empieza en el último mes real completo para que se vea la continuidad.
    """
    valores = [v for v in [*reales, *proyectados] if v is not None]
    maximo = tope(max(valores, default=0))
    n = len(etiquetas)
    ancho_plot, alto_plot = ANCHO - M_IZQ - M_DER, ALTO - M_SUP - M_INF
    xs = [M_IZQ + (ancho_plot * i / (n - 1) if n > 1 else ancho_plot / 2) for i in range(n)]

    def y(v):
        return M_SUP + alto_plot * (1 - float(v) / maximo)

    partes = _ejes(maximo, etiquetas, xs, alto_plot)
    puntos = [(xs[i], y(v), etiquetas[i], v) for i, v in enumerate(reales) if v is not None]
    if puntos:
        linea = " ".join(f"{x:.1f},{yy:.1f}" for x, yy, _, _ in puntos)
        base = M_SUP + alto_plot
        partes.append(f'<path d="M{puntos[0][0]:.1f},{base:.1f} L{linea.replace(" ", " L")} L{puntos[-1][0]:.1f},{base:.1f} Z" '
                      f'fill="{AZUL}" fill-opacity="0.08"/>')
        partes.append(f'<polyline points="{linea}" fill="none" stroke="{AZUL}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    proy = [(xs[i], y(v), etiquetas[i], v) for i, v in enumerate(proyectados) if v is not None]
    if proy:
        partes.append(f'<polyline points="{" ".join(f"{x:.1f},{yy:.1f}" for x, yy, _, _ in proy)}" fill="none" stroke="{AZUL}" '
                      f'stroke-width="2" stroke-dasharray="6 5" stroke-linecap="round"/>')
    for x, yy, etiqueta, v in puntos:
        partes.append(_punto(x, yy, f"{etiqueta}: {dinero(v)}"))
    reales_x = {x for x, *_ in puntos}
    for x, yy, etiqueta, v in proy:
        if x not in reales_x:
            partes.append(_punto(x, yy, f"{etiqueta} (proyección): {dinero(v)}", hueco=True))
    if puntos:
        x, yy, _, v = max(puntos, key=lambda p: p[3])
        partes.append(_etiqueta(x, yy, compacto(v)))
    if proy:
        x, yy, _, v = proy[-1]
        partes.append(_etiqueta(x, yy, compacto(v), "end"))
    return _svg("Ventas por mes", "Total vendido por mes; la línea punteada es la proyección.", partes)


def acumulado_mes(dias_mes: int, diario, proyeccion=None, meta=None) -> str:
    """Total vendido acumulado día a día, su proyección al fin de mes y la meta."""
    valores = [v for _, v in diario] + [v for v in (proyeccion, meta) if v is not None]
    maximo = tope(max(valores, default=0))
    ancho_plot, alto_plot = ANCHO - M_IZQ - M_DER, ALTO - M_SUP - M_INF

    def x(dia):
        return M_IZQ + ancho_plot * (dia - 1) / max(dias_mes - 1, 1)

    def y(v):
        return M_SUP + alto_plot * (1 - float(v) / maximo)

    marcas = sorted({1, 5, 10, 15, 20, 25, dias_mes})
    partes = _ejes(maximo, [str(d) for d in marcas], [x(d) for d in marcas], alto_plot)
    if meta is not None:
        partes.append(f'<line x1="{M_IZQ}" x2="{ANCHO - M_DER}" y1="{y(meta):.1f}" y2="{y(meta):.1f}" stroke="{GRIS_META}" '
                      f'stroke-width="1.5" stroke-dasharray="3 4"/>')
        partes.append(f'<text x="{ANCHO - M_DER}" y="{y(meta) - 6:.1f}" text-anchor="end" font-size="11" fill="{TEXTO}">'
                      f'Meta {escape(compacto(meta))}</text>')
    if diario:
        linea = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in diario)
        base = M_SUP + alto_plot
        partes.append(f'<path d="M{x(diario[0][0]):.1f},{base:.1f} L{linea.replace(" ", " L")} L{x(diario[-1][0]):.1f},{base:.1f} Z" '
                      f'fill="{AZUL}" fill-opacity="0.08"/>')
        partes.append(f'<polyline points="{linea}" fill="none" stroke="{AZUL}" stroke-width="2" stroke-linejoin="round"/>')
        ultimo_dia, ultimo = diario[-1]
        if proyeccion is not None and ultimo_dia < dias_mes:
            partes.append(f'<line x1="{x(ultimo_dia):.1f}" y1="{y(ultimo):.1f}" x2="{x(dias_mes):.1f}" y2="{y(proyeccion):.1f}" '
                          f'stroke="{AZUL}" stroke-width="2" stroke-dasharray="6 5" stroke-linecap="round"/>')
            partes.append(_punto(x(dias_mes), y(proyeccion), f"Proyección al día {dias_mes}: {dinero(proyeccion)}", hueco=True))
            partes.append(_etiqueta(x(dias_mes), y(proyeccion), compacto(proyeccion), "end"))
        partes.append(_punto(x(ultimo_dia), y(ultimo), f"Día {ultimo_dia}: {dinero(ultimo)} acumulado"))
        partes.append(_etiqueta(x(ultimo_dia), y(ultimo), compacto(ultimo)))
    return _svg("Ventas acumuladas del mes", "Total vendido acumulado por día, proyección al fin de mes y meta.", partes)
