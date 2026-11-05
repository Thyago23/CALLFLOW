from django.db import migrations

SQL = """
-- 1. Cada movimiento actualiza el stock del material (bloqueando la fila para evitar
--    carreras entre dos usuarios), rechaza salidas mayores al disponible y avisa a los
--    administradores cuando el material cae a su nivel mínimo.
CREATE OR REPLACE FUNCTION inventario_aplicar_movimiento() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    m RECORD;
    v_nuevo numeric(12, 2);
BEGIN
    SELECT id, nombre, stock_actual, stock_minimo, activo INTO m
    FROM inventario_material WHERE id = NEW.material_id FOR UPDATE;

    IF NOT m.activo THEN
        RAISE EXCEPTION 'El material "%" está inactivo y no admite movimientos', m.nombre
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.tipo IN ('ENTRADA', 'AJUSTE_POSITIVO') THEN
        v_nuevo := m.stock_actual + NEW.cantidad;
    ELSE
        IF NEW.cantidad > m.stock_actual THEN
            RAISE EXCEPTION 'Stock insuficiente de "%": disponible %, solicitado %', m.nombre, m.stock_actual, NEW.cantidad
                USING ERRCODE = 'check_violation';
        END IF;
        v_nuevo := m.stock_actual - NEW.cantidad;
    END IF;

    UPDATE inventario_material SET stock_actual = v_nuevo, actualizado_en = now() WHERE id = m.id;
    NEW.stock_resultante := v_nuevo;

    IF v_nuevo <= m.stock_minimo AND m.stock_actual > m.stock_minimo THEN
        INSERT INTO core_notificacion (destinatario_id, tipo, titulo, mensaje, url, leida, creado_en)
        SELECT u.id, 'STOCK_BAJO', left('Stock bajo: ' || m.nombre, 150),
               format('Quedan %s (mínimo configurado: %s). Conviene reponer.', v_nuevo, m.stock_minimo),
               '', false, now()
        FROM core_usuario u
        WHERE u.rol = 'ADMINISTRADOR' AND u.is_active;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_movimiento_aplicar
    BEFORE INSERT ON inventario_movimientoinventario
    FOR EACH ROW EXECUTE FUNCTION inventario_aplicar_movimiento();

-- 2. El libro de movimientos es inmutable: los errores se corrigen con un ajuste.
CREATE OR REPLACE FUNCTION inventario_movimiento_inmutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Los movimientos de inventario no se modifican ni eliminan; registre un ajuste'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER trg_movimiento_inmutable
    BEFORE UPDATE OR DELETE ON inventario_movimientoinventario
    FOR EACH ROW EXECUTE FUNCTION inventario_movimiento_inmutable();

-- 3. El stock solo cambia a través de movimientos: un material nuevo empieza en 0 y una
--    edición directa (o una copia desactualizada guardada desde la aplicación) no lo altera.
CREATE OR REPLACE FUNCTION inventario_proteger_stock() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.stock_actual := 0;
    ELSIF pg_trigger_depth() = 1 THEN
        NEW.stock_actual := OLD.stock_actual;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_material_proteger_stock
    BEFORE INSERT OR UPDATE ON inventario_material
    FOR EACH ROW EXECUTE FUNCTION inventario_proteger_stock();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_material_proteger_stock ON inventario_material;
DROP TRIGGER IF EXISTS trg_movimiento_inmutable ON inventario_movimientoinventario;
DROP TRIGGER IF EXISTS trg_movimiento_aplicar ON inventario_movimientoinventario;
DROP FUNCTION IF EXISTS inventario_proteger_stock();
DROP FUNCTION IF EXISTS inventario_movimiento_inmutable();
DROP FUNCTION IF EXISTS inventario_aplicar_movimiento();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventario", "0001_initial"),
        ("core", "0001_initial"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
