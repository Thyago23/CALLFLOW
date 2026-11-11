"""
Estadísticas y proyección de ventas (SCRUM-67 y SCRUM-68).

Las ventas se cuentan en el mes en que se envió el DAMI (la fecha de su fila en la hoja de
producción), igual que la hoja, las metas y las comisiones.

Proyecciones (solo para el mes en curso, que es el que todavía puede cambiar):
- Cierre del mes: lo vendido hasta hoy ÷ días laborables transcurridos × días laborables del
  mes (ritmo diario). Se compara con la suma de las metas.
- Próximos 3 meses: tendencia lineal (mínimos cuadrados) sobre los meses completos con
  actividad, junto al promedio de los últimos 3 meses. Con menos de 3 meses se avisa que la
  tendencia es poco confiable.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractMonth, ExtractYear
from django.utils import timezone

from apps.core import jornada

from .catalogo import equipos_al

MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MESES_HISTORIA = 12
MESES_PROYECTADOS = 3
CERO = Decimal("0")


def sumar_meses(anio: int, mes: int, n: int) -> tuple[int, int]:
    total = anio * 12 + (mes - 1) + n
    return total // 12, total % 12 + 1


def ultimo_dia(anio: int, mes: int) -> date:
    siguiente = sumar_meses(anio, mes, 1)
    return date(*siguiente, 1) - timedelta(days=1)


def etiqueta_mes(anio: int, mes: int) -> str:
    return f"{MESES_CORTOS[mes - 1]} {anio % 100:02d}"


def tendencia_lineal(valores: list, pasos: int, desde: int | None = None) -> list[Decimal]:
    """
    Recta de mínimos cuadrados sobre (0, v0), (1, v1)… y su valor en los `pasos` puntos
    siguientes (desde la posición `desde`, por defecto justo después del último). Nunca negativa.
    """
    n = len(valores)
    desde = n if desde is None else desde
    if n == 0:
        return [CERO] * pasos
    ys = [float(v) for v in valores]
    if n == 1:
        return [Decimal(str(round(ys[0], 2)))] * pasos
    media_x, media_y = (n - 1) / 2, sum(ys) / n
    pendiente = sum((x - media_x) * (y - media_y) for x, y in enumerate(ys)) / sum((x - media_x) ** 2 for x in range(n))
    corte = media_y - pendiente * media_x
    return [max(Decimal(str(round(corte + pendiente * x, 2))), CERO) for x in range(desde, desde + pasos)]


def dias_laborables(desde: date, hasta: date) -> int:
    return sum(1 for i in range((hasta - desde).days + 1) if jornada.es_laborable(desde + timedelta(days=i)))


@dataclass
class Mes:
    anio: int
    mes: int
    total: Decimal = CERO
    cerradas: int = 0
    no_concretadas: int = 0
    enviados: int = 0

    @property
    def etiqueta(self) -> str:
        return etiqueta_mes(self.anio, self.mes)

    @property
    def gestionadas(self) -> int:
        return self.cerradas + self.no_concretadas

    @property
    def tasa(self):
        return Decimal(self.cerradas * 100) / self.gestionadas if self.gestionadas else None

    @property
    def ticket(self):
        return self.total / self.cerradas if self.cerradas else None


@dataclass
class Estadisticas:
    anio: int
    mes: int
    hoy: date
    serie: list
    ranking: list
    equipos: list
    diario: list = field(default_factory=list)  # [(día, acumulado)]
    meta: Decimal | None = None
    en_curso: bool = False
    laborables: int = 0
    transcurridos: int = 0
    proyeccion_mes: Decimal | None = None
    proximos: list = field(default_factory=list)  # [{"etiqueta", "tendencia", "promedio"}]
    meses_base: int = 0

    @property
    def actual(self) -> Mes:
        return self.serie[-1]

    @property
    def anterior(self) -> Mes:
        return self.serie[-2]

    @property
    def variacion(self):
        """% de cambio del total vendido frente al mes anterior."""
        antes = self.anterior.total
        return (self.actual.total - antes) * 100 / antes if antes else None

    @property
    def embudo(self) -> list:
        a = self.actual
        return [("DAMI enviados", a.enviados), ("Gestionados por cierre", a.gestionadas), ("Ventas cerradas", a.cerradas)]

    @property
    def confiable(self) -> bool:
        return self.meses_base >= 3

    @property
    def avance_meta(self):
        return self.actual.total * 100 / self.meta if self.meta else None

    @property
    def proyeccion_vs_meta(self):
        return self.proyeccion_mes * 100 / self.meta if self.meta and self.proyeccion_mes is not None else None

    @property
    def ancho_vendido(self) -> int:
        """Para el medidor de la meta (0 a 100)."""
        return min(round(self.avance_meta or 0), 100)

    @property
    def ancho_proyectado(self) -> int:
        return min(round(self.proyeccion_vs_meta or 0), 100)

    @property
    def ritmo_necesario(self):
        """Lo que hay que vender por día laborable restante para llegar a la meta."""
        if not (self.en_curso and self.meta):
            return None
        restantes = self.laborables - self.transcurridos
        falta = self.meta - self.actual.total
        if falta <= 0:
            return CERO
        return falta / restantes if restantes > 0 else None

    @property
    def promedio_3(self):
        completos = self.serie[:-1] if self.en_curso else self.serie
        ultimos = [m.total for m in completos[-3:]]
        return sum(ultimos, CERO) / len(ultimos) if ultimos else None


def _vendedores(anio, mes, equipo=None, vendedor=None):
    """Ids de vendedores del filtro (None = todos)."""
    if vendedor:
        return [vendedor.pk]
    if equipo:
        return [eid for eid, esquema in equipos_al(ultimo_dia(anio, mes)).items() if esquema.pk == equipo.pk]
    return None


def calcular(anio: int, mes: int, equipo=None, vendedor=None, hoy: date | None = None) -> Estadisticas:
    from apps.cierre.models import MetaMensual
    from apps.personal.models import Empleado
    from apps.ventas.models import EstadoProduccion as P, RegistroProduccion

    hoy = hoy or timezone.localdate()
    inicio_mes, fin_mes = date(anio, mes, 1), ultimo_dia(anio, mes)
    ids = _vendedores(anio, mes, equipo, vendedor)
    base = RegistroProduccion.objects.all()
    if ids is not None:
        base = base.filter(hoja__vendedor__in=ids)
    cerrada = Q(estado=P.VENTA_CERRADA)
    metricas = {"total": Sum("valor_venta", filter=cerrada), "cerradas": Count("pk", filter=cerrada),
                "no_concretadas": Count("pk", filter=Q(estado=P.NO_CONCRETADA)), "enviados": Count("pk", filter=~Q(estado=P.ANULADO))}

    # Serie de los últimos 12 meses (hasta el mes elegido)
    primero = date(*sumar_meses(anio, mes, -(MESES_HISTORIA - 1)), 1)
    agrupado = {(d["a"], d["m"]): d for d in base.filter(fecha__range=(primero, fin_mes))
                .annotate(a=ExtractYear("fecha"), m=ExtractMonth("fecha")).values("a", "m").annotate(**metricas)}
    serie = []
    for i in range(MESES_HISTORIA):
        a, m = sumar_meses(primero.year, primero.month, i)
        d = agrupado.get((a, m), {})
        serie.append(Mes(a, m, d.get("total") or CERO, d.get("cerradas", 0), d.get("no_concretadas", 0), d.get("enviados", 0)))

    # Ranking y equipos del mes elegido
    del_mes = base.filter(fecha__range=(inicio_mes, fin_mes))
    filas = list(del_mes.values("hoja__vendedor").annotate(**metricas))
    nombres = {e.pk: e for e in Empleado.objects.filter(pk__in=[f["hoja__vendedor"] for f in filas])}
    metas = dict(MetaMensual.objects.filter(anio=anio, mes=mes).values_list("vendedor_id", "monto_meta"))
    ranking = sorted(({"vendedor": nombres[f["hoja__vendedor"]], "total": f["total"] or CERO, "cerradas": f["cerradas"],
                       "enviados": f["enviados"], "meta": metas.get(f["hoja__vendedor"])} for f in filas),
                     key=lambda r: (-r["total"], -r["cerradas"], str(r["vendedor"])))
    equipos_vigentes = equipos_al(fin_mes)
    por_equipo = {}
    for r in ranking:
        nombre = str(equipos_vigentes.get(r["vendedor"].pk) or "Administrativo")
        por_equipo[nombre] = por_equipo.get(nombre, CERO) + r["total"]
    equipos = sorted(({"equipo": k, "total": v} for k, v in por_equipo.items()), key=lambda e: -e["total"])

    meta_ids = ids if ids is not None else list(metas)
    meta = sum((metas[i] for i in meta_ids if i in metas), CERO) if any(i in metas for i in meta_ids) else None

    est = Estadisticas(anio=anio, mes=mes, hoy=hoy, serie=serie, ranking=ranking, equipos=equipos, meta=meta,
                       en_curso=(anio, mes) == (hoy.year, hoy.month))

    # Ventas cerradas día a día (acumulado), hasta hoy si es el mes en curso
    if inicio_mes <= hoy:
        hasta = min(hoy, fin_mes)
        por_dia = dict(del_mes.filter(cerrada).values_list("fecha").annotate(t=Sum("valor_venta")))
        acumulado = CERO
        for dia in range(1, hasta.day + 1):
            acumulado += por_dia.get(date(anio, mes, dia)) or CERO
            est.diario.append((dia, acumulado))

    if est.en_curso:
        est.laborables = dias_laborables(inicio_mes, fin_mes)
        est.transcurridos = dias_laborables(inicio_mes, hoy)
        if est.transcurridos:
            factor = Decimal(est.laborables) / est.transcurridos
            est.proyeccion_mes = (est.actual.total * factor).quantize(Decimal("0.01"))
            for r in ranking:
                r["proyeccion"] = (r["total"] * factor).quantize(Decimal("0.01"))
        # Tendencia sobre los meses completos con actividad (sin los meses vacíos anteriores al inicio)
        completos = serie[:-1]
        while completos and not completos[0].enviados:
            completos = completos[1:]
        est.meses_base = len(completos)
        tendencia = tendencia_lineal([m.total for m in completos], MESES_PROYECTADOS, desde=len(completos) + 1)
        promedio = est.promedio_3
        for i in range(MESES_PROYECTADOS):
            a, m = sumar_meses(anio, mes, i + 1)
            est.proximos.append({"etiqueta": etiqueta_mes(a, m), "tendencia": tendencia[i] if completos else None,
                                 "promedio": promedio})
    return est
