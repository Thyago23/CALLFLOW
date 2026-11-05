from django.db import migrations

from apps.core.sql import auditar

SQL_TRIGGERS = """
-- 1. Antes de registrar una gestión: el formulario debe estar en cola, lo debe gestionar el
--    encargado asignado (si lo hay) y nadie cierra su propio formulario. La fila del
--    formulario se bloquea para que los dos encargados no lo cierren a la vez.
CREATE OR REPLACE FUNCTION cierre_gestion_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    f RECORD;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.formulario_id <> OLD.formulario_id THEN
        RAISE EXCEPTION 'No se puede cambiar el formulario de una gestión de cierre'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT estado, encargado_asignado_id, vendedor_id INTO f
    FROM ventas_formulariodami WHERE id = NEW.formulario_id FOR UPDATE;

    IF TG_OP = 'INSERT' AND f.estado NOT IN ('ENVIADO', 'EN_GESTION') THEN
        RAISE EXCEPTION 'El formulario DAMI % no está pendiente de cierre (estado: %)', NEW.formulario_id, f.estado
            USING ERRCODE = 'check_violation';
    END IF;
    IF f.encargado_asignado_id IS NOT NULL AND f.encargado_asignado_id <> NEW.encargado_id THEN
        RAISE EXCEPTION 'El formulario DAMI % está asignado a otro encargado de cierre', NEW.formulario_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.encargado_id = f.vendedor_id THEN
        RAISE EXCEPTION 'Un vendedor no puede cerrar su propio formulario DAMI'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_gestion_validar
    BEFORE INSERT OR UPDATE ON cierre_gestioncierre
    FOR EACH ROW EXECUTE FUNCTION cierre_gestion_validar();

-- 2. Después de registrar la gestión: se actualiza el formulario y la hoja de producción
--    del vendedor (estado y valor de la venta). Así los totales siempre cuadran.
CREATE OR REPLACE FUNCTION cierre_gestion_sincronizar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE ventas_formulariodami
    SET estado = NEW.resultado, actualizado_en = now()
    WHERE id = NEW.formulario_id AND estado IS DISTINCT FROM NEW.resultado;

    UPDATE ventas_registroproduccion
    SET estado = NEW.resultado, valor_venta = NEW.valor_venta, actualizado_en = now()
    WHERE formulario_id = NEW.formulario_id;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_gestion_sincronizar
    AFTER INSERT OR UPDATE ON cierre_gestioncierre
    FOR EACH ROW EXECUTE FUNCTION cierre_gestion_sincronizar();

-- 3. Una gestión de cierre no se elimina (se corrige actualizándola, y queda auditada).
CREATE OR REPLACE FUNCTION cierre_gestion_no_eliminar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Las gestiones de cierre no se eliminan; corrija el resultado si es necesario'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER trg_gestion_no_eliminar
    BEFORE DELETE ON cierre_gestioncierre
    FOR EACH ROW EXECUTE FUNCTION cierre_gestion_no_eliminar();
"""

REVERSO_TRIGGERS = """
DROP TRIGGER IF EXISTS trg_gestion_no_eliminar ON cierre_gestioncierre;
DROP TRIGGER IF EXISTS trg_gestion_sincronizar ON cierre_gestioncierre;
DROP TRIGGER IF EXISTS trg_gestion_validar ON cierre_gestioncierre;
DROP FUNCTION IF EXISTS cierre_gestion_no_eliminar();
DROP FUNCTION IF EXISTS cierre_gestion_sincronizar();
DROP FUNCTION IF EXISTS cierre_gestion_validar();
"""

# Panel general: ventas acumuladas, metas, valor pendiente y ranking por vendedor y mes.
SQL_VISTA = """
CREATE OR REPLACE VIEW cierre_v_resumen_vendedor_mes AS
WITH claves AS (
    SELECT vendedor_id, anio, mes FROM ventas_hojaproduccion
    UNION
    SELECT vendedor_id, anio, mes FROM cierre_metamensual
),
produccion AS (
    SELECT h.vendedor_id, h.anio, h.mes,
           COUNT(r.id)                                               AS total_formularios,
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
       RANK() OVER (PARTITION BY anio, mes ORDER BY total_vendido DESC)::int AS posicion
FROM resumen;
"""

REVERSO_VISTA = "DROP VIEW IF EXISTS cierre_v_resumen_vendedor_mes;"


class Migration(migrations.Migration):
    dependencies = [
        ("cierre", "0002_initial"),
        ("ventas", "0002_triggers_dami"),
        ("core", "0002_auditoria"),
    ]

    operations = [
        migrations.RunSQL(SQL_TRIGGERS, REVERSO_TRIGGERS),
        migrations.RunSQL(SQL_VISTA, REVERSO_VISTA),
        migrations.RunSQL(*auditar("cierre_gestioncierre")),
        migrations.RunSQL(*auditar("cierre_metamensual")),
    ]
