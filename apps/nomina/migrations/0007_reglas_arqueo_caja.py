"""
Reglas de la caja chica con el formato de la empresa (hojas «Caja chica» y «Arqueo de caja»):
- La categoría y la forma de pago de un movimiento tampoco se modifican (se anula y se registra otro).
- El arqueo lo calcula la base de datos: saldo según registro (movimientos vigentes hasta la fecha del
  arqueo), total contado (cantidad × denominación) y diferencia. Lo que mande la aplicación se ignora.
- Un arqueo no se modifica ni se borra, y queda en la auditoría.
"""
from django.db import migrations

from apps.core.sql import auditar

CAJA = """
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
        IF (NEW.fecha, NEW.tipo, NEW.categoria, NEW.concepto, NEW.beneficiario, NEW.comprobante, NEW.valor, NEW.forma_pago)
           IS DISTINCT FROM (OLD.fecha, OLD.tipo, OLD.categoria, OLD.concepto, OLD.beneficiario, OLD.comprobante, OLD.valor, OLD.forma_pago) THEN
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
"""

# Mismo cuerpo que en 0005, para poder revertir.
CAJA_REVERSO = CAJA.replace("NEW.categoria, ", "").replace("OLD.categoria, ", "").replace(", NEW.forma_pago", "").replace(", OLD.forma_pago", "")

ARQUEO = """
CREATE OR REPLACE FUNCTION nomina_arqueo_calcular() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_clave text;
    v_cantidad text;
    v_total numeric(14, 2) := 0;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Los arqueos de caja chica no se modifican ni se eliminan; registre un arqueo nuevo'
            USING ERRCODE = 'check_violation';
    END IF;

    IF jsonb_typeof(NEW.conteo) <> 'object' THEN
        RAISE EXCEPTION 'El conteo del arqueo debe indicar la cantidad por denominación' USING ERRCODE = 'check_violation';
    END IF;
    FOR v_clave, v_cantidad IN SELECT key, value FROM jsonb_each_text(NEW.conteo) LOOP
        IF v_clave NOT IN ('100', '50', '20', '10', '5', '1', '0.50', '0.25', '0.10', '0.05', '0.01') THEN
            RAISE EXCEPTION 'Denominación no válida en el arqueo: %', v_clave USING ERRCODE = 'check_violation';
        END IF;
        IF v_cantidad !~ '^[0-9]{1,6}$' THEN
            RAISE EXCEPTION 'La cantidad de billetes o monedas de % debe ser un número entero no negativo', v_clave
                USING ERRCODE = 'check_violation';
        END IF;
        v_total := v_total + v_clave::numeric * v_cantidad::integer;
    END LOOP;

    -- Que no entre un movimiento a mitad del cálculo.
    PERFORM pg_advisory_xact_lock(hashtext('nomina_caja_chica'));

    SELECT COALESCE(SUM(CASE WHEN tipo = 'INGRESO' THEN valor ELSE -valor END), 0) INTO NEW.saldo_registro
    FROM nomina_movimientocajachica
    WHERE NOT anulado AND fecha <= NEW.fecha;

    NEW.total_contado := v_total;
    NEW.diferencia := v_total - NEW.saldo_registro;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_arqueo_calcular
    BEFORE INSERT OR UPDATE OR DELETE ON nomina_arqueocaja
    FOR EACH ROW EXECUTE FUNCTION nomina_arqueo_calcular();
"""

ARQUEO_REVERSO = """
DROP TRIGGER IF EXISTS trg_arqueo_calcular ON nomina_arqueocaja;
DROP FUNCTION IF EXISTS nomina_arqueo_calcular();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("nomina", "0006_caja_chica_formato"),
        ("core", "0002_auditoria"),
    ]

    operations = [
        migrations.RunSQL(CAJA, CAJA_REVERSO),
        migrations.RunSQL(ARQUEO, ARQUEO_REVERSO),
        migrations.RunSQL(*auditar("nomina_arqueocaja")),
    ]
