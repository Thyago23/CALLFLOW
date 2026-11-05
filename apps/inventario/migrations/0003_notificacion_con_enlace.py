from django.db import migrations

# La notificación de stock bajo ahora lleva al material (antes no tenía enlace).
SQL = """
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
               '/inventario/materiales/' || m.id || '/', false, now()
        FROM core_usuario u
        WHERE u.rol = 'ADMINISTRADOR' AND u.is_active;
    END IF;

    RETURN NEW;
END;
$$;
"""

REVERSO = """
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
"""


class Migration(migrations.Migration):
    dependencies = [("inventario", "0002_triggers_stock")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
