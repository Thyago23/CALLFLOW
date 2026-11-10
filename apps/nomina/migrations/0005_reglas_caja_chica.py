"""
Reglas de la caja chica que garantiza la base de datos:
- El saldo nunca queda negativo (ni al registrar un gasto ni al anular un ingreso).
- Un movimiento no se modifica ni se borra: solo se anula, con motivo, y una sola vez.
- Todos los cambios quedan en la auditoría (core_registrocambio).
"""
from django.db import migrations

from apps.core.sql import auditar

SQL = """
CREATE OR REPLACE FUNCTION nomina_caja_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_saldo numeric(14, 2);
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Los movimientos de caja chica no se eliminan; anúlelos indicando el motivo'
            USING ERRCODE = 'check_violation';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF OLD.anulado THEN
            RAISE EXCEPTION 'El movimiento de caja chica % ya está anulado y no se puede modificar', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.fecha, NEW.tipo, NEW.concepto, NEW.beneficiario, NEW.comprobante, NEW.valor)
           IS DISTINCT FROM (OLD.fecha, OLD.tipo, OLD.concepto, OLD.beneficiario, OLD.comprobante, OLD.valor) THEN
            RAISE EXCEPTION 'Los movimientos de caja chica no se modifican; anule este y registre uno nuevo'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- Un movimiento a la vez: así dos personas no gastan el mismo saldo.
    PERFORM pg_advisory_xact_lock(hashtext('nomina_caja_chica'));

    SELECT COALESCE(SUM(CASE WHEN tipo = 'INGRESO' THEN valor ELSE -valor END), 0) INTO v_saldo
    FROM nomina_movimientocajachica
    WHERE NOT anulado AND id IS DISTINCT FROM NEW.id;

    IF NOT NEW.anulado THEN
        v_saldo := v_saldo + CASE WHEN NEW.tipo = 'INGRESO' THEN NEW.valor ELSE -NEW.valor END;
    END IF;

    IF v_saldo < 0 THEN
        RAISE EXCEPTION 'Saldo de caja chica insuficiente: quedaría en %', v_saldo
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_caja_validar
    BEFORE INSERT OR UPDATE OR DELETE ON nomina_movimientocajachica
    FOR EACH ROW EXECUTE FUNCTION nomina_caja_validar();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_caja_validar ON nomina_movimientocajachica;
DROP FUNCTION IF EXISTS nomina_caja_validar();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("nomina", "0004_caja_chica"),
        ("core", "0002_auditoria"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSO),
        migrations.RunSQL(*auditar("nomina_movimientocajachica")),
    ]
