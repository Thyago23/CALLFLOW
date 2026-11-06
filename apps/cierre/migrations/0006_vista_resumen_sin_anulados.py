"""
SCRUM-54 y SCRUM-57: la vista del panel comercial ya no cuenta los formularios anulados
como enviados, y el ranking desempata por número de ventas cerradas.
"""
from importlib import import_module

from django.db import migrations

VISTA_ANTERIOR = import_module("apps.cierre.migrations.0003_triggers_y_vista").SQL_VISTA

VISTA = """
CREATE OR REPLACE VIEW cierre_v_resumen_vendedor_mes AS
WITH claves AS (
    SELECT vendedor_id, anio, mes FROM ventas_hojaproduccion
    UNION
    SELECT vendedor_id, anio, mes FROM cierre_metamensual
),
produccion AS (
    SELECT h.vendedor_id, h.anio, h.mes,
           COUNT(r.id) FILTER (WHERE r.estado <> 'ANULADO')          AS total_formularios,
           COUNT(r.id) FILTER (WHERE r.estado = 'VENTA_CERRADA')     AS ventas_cerradas,
           COUNT(r.id) FILTER (WHERE r.estado = 'NO_CONCRETADA')     AS ventas_no_concretadas,
           COUNT(r.id) FILTER (WHERE r.estado = 'PENDIENTE')         AS pendientes,
           COALESCE(SUM(r.valor_venta) FILTER (WHERE r.estado = 'VENTA_CERRADA'), 0) AS total_vendido
    FROM ventas_hojaproduccion h
    LEFT JOIN ventas_registroproduccion r ON r.hoja_id = h.id
    GROUP BY h.vendedor_id, h.anio, h.mes
),
resumen AS (
    SELECT c.vendedor_id, c.anio, c.mes,
           COALESCE(p.total_formularios, 0)::int      AS total_formularios,
           COALESCE(p.ventas_cerradas, 0)::int        AS ventas_cerradas,
           COALESCE(p.ventas_no_concretadas, 0)::int  AS ventas_no_concretadas,
           COALESCE(p.pendientes, 0)::int             AS pendientes,
           COALESCE(p.total_vendido, 0)::numeric(12, 2) AS total_vendido,
           m.monto_meta
    FROM claves c
    LEFT JOIN produccion p USING (vendedor_id, anio, mes)
    LEFT JOIN cierre_metamensual m USING (vendedor_id, anio, mes)
)
SELECT vendedor_id || '-' || anio || '-' || mes AS id,
       vendedor_id, anio, mes,
       total_formularios, ventas_cerradas, ventas_no_concretadas, pendientes,
       total_vendido,
       monto_meta,
       CASE WHEN monto_meta IS NULL THEN NULL
            ELSE GREATEST(monto_meta - total_vendido, 0)::numeric(12, 2) END AS pendiente_meta,
       CASE WHEN monto_meta IS NULL THEN NULL
            ELSE LEAST(ROUND(total_vendido * 100 / monto_meta, 2), 99999.99)::numeric(7, 2) END AS porcentaje_meta,
       RANK() OVER (PARTITION BY anio, mes ORDER BY total_vendido DESC, ventas_cerradas DESC)::int AS posicion
FROM resumen;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("cierre", "0005_movimiento_asignacion"),
    ]

    operations = [
        migrations.RunSQL(VISTA, VISTA_ANTERIOR),
    ]
