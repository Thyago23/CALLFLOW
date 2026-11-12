"""
Reglas de los recibos de caja chica que garantiza la base de datos:
- Las filas de un recibo solo se agregan mientras está abierto; no se modifican ni se borran: se anulan (una vez).
- Un recibo no se borra. Mientras está abierto no tiene devoluciones ni reembolsos. Al liquidarse ya no vuelve a
  abrirse ni cambia, salvo para registrar más devolución, y nunca se devuelve ni reembolsa más de lo que corresponde
  según sus filas (las mismas fórmulas del Excel).
- Todo queda en la auditoría.
"""
from django.db import migrations

from apps.core.sql import auditar

SQL = """
CREATE OR REPLACE FUNCTION nomina_linea_recibo_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_estado varchar;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Las filas de un recibo de caja chica no se eliminan; anúlelas' USING ERRCODE = 'check_violation';
    END IF;
    SELECT estado INTO v_estado FROM nomina_recibocaja WHERE id = NEW.recibo_id FOR UPDATE;
    IF v_estado <> 'ABIERTO' THEN
        RAISE EXCEPTION 'El recibo de caja chica ya está liquidado: no se pueden agregar ni anular filas' USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.anulada THEN
            RAISE EXCEPTION 'La fila % del recibo ya está anulada', OLD.id USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.recibo_id, NEW.fecha, NEW.concepto, NEW.comprobante, NEW.valor_entregado, NEW.valor_gastado, NEW.movimiento_id)
           IS DISTINCT FROM (OLD.recibo_id, OLD.fecha, OLD.concepto, OLD.comprobante, OLD.valor_entregado, OLD.valor_gastado, OLD.movimiento_id) THEN
            RAISE EXCEPTION 'Las filas de un recibo de caja chica no se modifican; anule la fila y agregue otra' USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.anulada THEN
        RAISE EXCEPTION 'Una fila nueva no puede nacer anulada' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_linea_recibo_validar
    BEFORE INSERT OR UPDATE OR DELETE ON nomina_linearecibo
    FOR EACH ROW EXECUTE FUNCTION nomina_linea_recibo_validar();

CREATE OR REPLACE FUNCTION nomina_recibo_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_entregado numeric(14, 2);
    v_gastado numeric(14, 2);
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Los recibos de caja chica no se eliminan' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.estado = 'ABIERTO' THEN
        IF TG_OP = 'UPDATE' AND OLD.estado = 'LIQUIDADO' THEN
            RAISE EXCEPTION 'Un recibo liquidado no se vuelve a abrir' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.valor_devuelto <> 0 OR NEW.valor_reembolsado <> 0 THEN
            RAISE EXCEPTION 'Las devoluciones y reembolsos se registran al liquidar el recibo' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- Liquidado: solo puede crecer lo devuelto, hasta lo que corresponde.
    IF TG_OP = 'UPDATE' AND OLD.estado = 'LIQUIDADO' THEN
        IF (NEW.fecha, NEW.entregado_a, NEW.area, NEW.autorizado_por, NEW.responsable_id, NEW.valor_reembolsado, NEW.liquidado_en)
           IS DISTINCT FROM (OLD.fecha, OLD.entregado_a, OLD.area, OLD.autorizado_por, OLD.responsable_id, OLD.valor_reembolsado, OLD.liquidado_en)
           OR NEW.valor_devuelto < OLD.valor_devuelto THEN
            RAISE EXCEPTION 'El recibo de caja chica ya está liquidado: solo se puede registrar el saldo pendiente de devolución'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    SELECT COALESCE(SUM(valor_entregado), 0), COALESCE(SUM(valor_gastado), 0) INTO v_entregado, v_gastado
    FROM nomina_linearecibo WHERE recibo_id = NEW.id AND NOT anulada;
    IF NEW.valor_devuelto > GREATEST(v_entregado - v_gastado, 0) THEN
        RAISE EXCEPTION 'No se puede devolver más que el valor a devolver (%)', GREATEST(v_entregado - v_gastado, 0)
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.valor_reembolsado <> GREATEST(v_gastado - v_entregado, 0) THEN
        RAISE EXCEPTION 'El reembolso debe ser el valor adicional a reembolsar (%)', GREATEST(v_gastado - v_entregado, 0)
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_recibo_validar
    BEFORE INSERT OR UPDATE OR DELETE ON nomina_recibocaja
    FOR EACH ROW EXECUTE FUNCTION nomina_recibo_validar();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_linea_recibo_validar ON nomina_linearecibo;
DROP FUNCTION IF EXISTS nomina_linea_recibo_validar();
DROP TRIGGER IF EXISTS trg_recibo_validar ON nomina_recibocaja;
DROP FUNCTION IF EXISTS nomina_recibo_validar();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("nomina", "0009_recibos_caja"),
        ("core", "0002_auditoria"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSO),
        migrations.RunSQL(*auditar("nomina_recibocaja")),
        migrations.RunSQL(*auditar("nomina_linearecibo")),
    ]
