"""
Reglas de las multas manuales que garantiza la base de datos:
- No se borran ni se modifican: una multa equivocada se anula, con motivo, y se registra otra.
- Solo se registran dentro del contrato del empleado (entre su ingreso y su salida).
- Todos los cambios quedan en la auditoría (core_registrocambio).
"""
from django.db import migrations

from apps.core.sql import auditar

SQL = """
CREATE OR REPLACE FUNCTION nomina_multa_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_ingreso date;
    v_salida date;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Las multas no se eliminan; anúlela indicando el motivo'
            USING ERRCODE = 'check_violation';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF OLD.anulada THEN
            RAISE EXCEPTION 'La multa % ya está anulada y no se puede modificar', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.empleado_id, NEW.fecha, NEW.concepto, NEW.valor, NEW.registrada_por_id)
           IS DISTINCT FROM (OLD.empleado_id, OLD.fecha, OLD.concepto, OLD.valor, OLD.registrada_por_id) THEN
            RAISE EXCEPTION 'Las multas no se modifican; anule esta y registre una nueva'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    SELECT fecha_ingreso, fecha_salida INTO v_ingreso, v_salida FROM personal_empleado WHERE id = NEW.empleado_id;
    IF NEW.fecha < v_ingreso OR (v_salida IS NOT NULL AND NEW.fecha > v_salida) THEN
        RAISE EXCEPTION 'La fecha de la multa está fuera del contrato del empleado'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_multa_validar
    BEFORE INSERT OR UPDATE OR DELETE ON nomina_multamanual
    FOR EACH ROW EXECUTE FUNCTION nomina_multa_validar();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_multa_validar ON nomina_multamanual;
DROP FUNCTION IF EXISTS nomina_multa_validar();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("nomina", "0014_multas_manuales"),
        ("core", "0002_auditoria"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSO),
        migrations.RunSQL(*auditar("nomina_multamanual")),
    ]
