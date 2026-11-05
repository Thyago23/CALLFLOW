from django.db import migrations

from apps.core.sql import auditar

SQL = """
-- 1. Flujo del formulario DAMI: transiciones de estado válidas y bloqueo optimista.
--    Las transiciones a VENTA_CERRADA / NO_CONCRETADA solo ocurren registrando una gestión
--    de cierre (cierre_gestioncierre), nunca editando el formulario a mano.
CREATE OR REPLACE FUNCTION ventas_formulario_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.estado NOT IN ('BORRADOR', 'ENVIADO') THEN
            RAISE EXCEPTION 'Un formulario DAMI nuevo debe crearse como BORRADOR o ENVIADO (recibido: %)', NEW.estado
                USING ERRCODE = 'check_violation';
        END IF;
        NEW.version := 1;
        RETURN NEW;
    END IF;

    -- Cada cambio incrementa la versión. La aplicación actualiza con
    -- "WHERE id = ? AND version = ?": si otro encargado guardó antes, no se sobrescribe nada.
    NEW.version := OLD.version + 1;

    IF NEW.estado IS DISTINCT FROM OLD.estado THEN
        IF NOT (
            (OLD.estado = 'BORRADOR' AND NEW.estado IN ('ENVIADO', 'ANULADO'))
            OR (OLD.estado IN ('ENVIADO', 'EN_GESTION')
                AND NEW.estado IN ('ENVIADO', 'EN_GESTION', 'VENTA_CERRADA', 'NO_CONCRETADA', 'ANULADO'))
            OR (OLD.estado IN ('VENTA_CERRADA', 'NO_CONCRETADA') AND NEW.estado IN ('VENTA_CERRADA', 'NO_CONCRETADA'))
        ) THEN
            RAISE EXCEPTION 'Transición no permitida para el formulario DAMI %: % -> %', OLD.id, OLD.estado, NEW.estado
                USING ERRCODE = 'check_violation';
        END IF;

        IF NEW.estado IN ('VENTA_CERRADA', 'NO_CONCRETADA') AND NOT EXISTS (
            SELECT 1 FROM cierre_gestioncierre g WHERE g.formulario_id = NEW.id AND g.resultado = NEW.estado
        ) THEN
            RAISE EXCEPTION 'El formulario DAMI % solo se cierra registrando una gestión de cierre', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF OLD.estado IN ('VENTA_CERRADA', 'NO_CONCRETADA', 'ANULADO')
       AND (NEW.vendedor_id, NEW.cliente_id, NEW.tiene_tarjeta_activa, NEW.datos_adicionales)
           IS DISTINCT FROM (OLD.vendedor_id, OLD.cliente_id, OLD.tiene_tarjeta_activa, OLD.datos_adicionales) THEN
        RAISE EXCEPTION 'El formulario DAMI % ya está finalizado y sus datos no se pueden modificar', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_formulario_validar
    BEFORE INSERT OR UPDATE ON ventas_formulariodami
    FOR EACH ROW EXECUTE FUNCTION ventas_formulario_validar();

-- 2. Al enviar el DAMI se crea automáticamente la fila en la hoja de producción del vendedor.
CREATE OR REPLACE FUNCTION ventas_formulario_produccion() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_hoja bigint;
    v_fecha date;
BEGIN
    IF NEW.estado = 'ENVIADO' AND (TG_OP = 'INSERT' OR OLD.estado = 'BORRADOR') THEN
        v_fecha := (NEW.enviado_en AT TIME ZONE 'America/Guayaquil')::date;

        INSERT INTO ventas_hojaproduccion (vendedor_id, anio, mes, creado_en, actualizado_en)
        VALUES (NEW.vendedor_id, EXTRACT(YEAR FROM v_fecha), EXTRACT(MONTH FROM v_fecha), now(), now())
        ON CONFLICT (vendedor_id, anio, mes) DO UPDATE SET actualizado_en = now()
        RETURNING id INTO v_hoja;

        INSERT INTO ventas_registroproduccion (hoja_id, formulario_id, fecha, estado, valor_venta, creado_en, actualizado_en)
        VALUES (v_hoja, NEW.id, v_fecha, 'PENDIENTE', NULL, now(), now())
        ON CONFLICT (formulario_id) DO NOTHING;
    ELSIF TG_OP = 'UPDATE' AND NEW.estado = 'ANULADO' AND OLD.estado <> 'ANULADO' THEN
        UPDATE ventas_registroproduccion
        SET estado = 'ANULADO', valor_venta = NULL, actualizado_en = now()
        WHERE formulario_id = NEW.id;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_formulario_produccion
    AFTER INSERT OR UPDATE OF estado ON ventas_formulariodami
    FOR EACH ROW EXECUTE FUNCTION ventas_formulario_produccion();

-- 3. Historial de cada cambio de estado, con el usuario que lo hizo.
CREATE OR REPLACE FUNCTION ventas_formulario_historial() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' OR NEW.estado IS DISTINCT FROM OLD.estado THEN
        INSERT INTO ventas_historialestadoformulario (formulario_id, estado_anterior, estado_nuevo, usuario_id, fecha)
        VALUES (
            NEW.id,
            CASE WHEN TG_OP = 'UPDATE' THEN OLD.estado ELSE '' END,
            NEW.estado,
            NULLIF(current_setting('callflow.usuario_id', true), '')::bigint,
            now()
        );
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_formulario_historial
    AFTER INSERT OR UPDATE OF estado ON ventas_formulariodami
    FOR EACH ROW EXECUTE FUNCTION ventas_formulario_historial();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_formulario_historial ON ventas_formulariodami;
DROP TRIGGER IF EXISTS trg_formulario_produccion ON ventas_formulariodami;
DROP TRIGGER IF EXISTS trg_formulario_validar ON ventas_formulariodami;
DROP FUNCTION IF EXISTS ventas_formulario_historial();
DROP FUNCTION IF EXISTS ventas_formulario_produccion();
DROP FUNCTION IF EXISTS ventas_formulario_validar();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ventas", "0001_initial"),
        ("core", "0002_auditoria"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSO),
        migrations.RunSQL(*auditar("ventas_registroproduccion")),
    ]
