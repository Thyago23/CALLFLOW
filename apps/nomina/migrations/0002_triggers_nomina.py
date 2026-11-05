from django.db import migrations

from apps.core.sql import auditar

SQL = """
-- 1. Un periodo CERRADO no admite cambios. Un periodo APROBADO o PAGADO ya no admite
--    cambios en sus novedades (las liquidaciones sí pueden pasar a PAGADA).
CREATE OR REPLACE FUNCTION nomina_validar_periodo() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_periodo bigint;
    v_estado text;
BEGIN
    FOREACH v_periodo IN ARRAY ARRAY[
        CASE WHEN TG_OP <> 'INSERT' THEN OLD.periodo_id END,
        CASE WHEN TG_OP <> 'DELETE' THEN NEW.periodo_id END
    ] LOOP
        CONTINUE WHEN v_periodo IS NULL;
        SELECT estado INTO v_estado FROM nomina_periodonomina WHERE id = v_periodo;
        IF v_estado = 'CERRADO' THEN
            RAISE EXCEPTION 'El periodo de nómina % está cerrado y no se puede modificar', v_periodo
                USING ERRCODE = 'check_violation';
        END IF;
        IF TG_TABLE_NAME = 'nomina_novedadnomina' AND v_estado IN ('APROBADO', 'PAGADO') THEN
            RAISE EXCEPTION 'El periodo de nómina % ya fue aprobado; no se pueden cambiar sus novedades', v_periodo
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_novedad_periodo_abierto
    BEFORE INSERT OR UPDATE OR DELETE ON nomina_novedadnomina
    FOR EACH ROW EXECUTE FUNCTION nomina_validar_periodo();

CREATE TRIGGER trg_liquidacion_periodo_abierto
    BEFORE INSERT OR UPDATE OR DELETE ON nomina_liquidacionnomina
    FOR EACH ROW EXECUTE FUNCTION nomina_validar_periodo();

-- 2. Un periodo cerrado queda congelado.
CREATE OR REPLACE FUNCTION nomina_periodo_congelado() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.estado = 'CERRADO' THEN
        RAISE EXCEPTION 'El periodo de nómina "%" está cerrado y no se puede modificar ni eliminar', OLD.nombre
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_periodo_congelado
    BEFORE UPDATE OR DELETE ON nomina_periodonomina
    FOR EACH ROW EXECUTE FUNCTION nomina_periodo_congelado();

-- 3. Solicitud de pago: la factura debe ser del mismo empleado, estar vigente, y el monto
--    no puede superar el neto de una liquidación aprobada.
CREATE OR REPLACE FUNCTION nomina_validar_solicitud_pago() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    liq RECORD;
    doc RECORD;
BEGIN
    SELECT empleado_id, estado, neto INTO liq FROM nomina_liquidacionnomina WHERE id = NEW.liquidacion_id;
    SELECT empleado_id, eliminado INTO doc FROM documentos_documento WHERE id = NEW.factura_id;

    IF liq.estado NOT IN ('APROBADA', 'PAGADA') THEN
        RAISE EXCEPTION 'Solo se puede solicitar el pago de una liquidación aprobada (estado actual: %)', liq.estado
            USING ERRCODE = 'check_violation';
    END IF;
    IF doc.eliminado THEN
        RAISE EXCEPTION 'La factura adjunta fue eliminada' USING ERRCODE = 'check_violation';
    END IF;
    IF doc.empleado_id IS DISTINCT FROM liq.empleado_id THEN
        RAISE EXCEPTION 'La factura debe pertenecer al mismo empleado de la liquidación'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.monto > liq.neto THEN
        RAISE EXCEPTION 'El monto solicitado (%) supera el neto de la liquidación (%)', NEW.monto, liq.neto
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_solicitud_pago_validar
    BEFORE INSERT OR UPDATE ON nomina_solicitudpago
    FOR EACH ROW EXECUTE FUNCTION nomina_validar_solicitud_pago();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_solicitud_pago_validar ON nomina_solicitudpago;
DROP TRIGGER IF EXISTS trg_periodo_congelado ON nomina_periodonomina;
DROP TRIGGER IF EXISTS trg_liquidacion_periodo_abierto ON nomina_liquidacionnomina;
DROP TRIGGER IF EXISTS trg_novedad_periodo_abierto ON nomina_novedadnomina;
DROP FUNCTION IF EXISTS nomina_validar_solicitud_pago();
DROP FUNCTION IF EXISTS nomina_periodo_congelado();
DROP FUNCTION IF EXISTS nomina_validar_periodo();
"""

TABLAS_AUDITADAS = [
    "nomina_reglacalculo",
    "nomina_periodonomina",
    "nomina_novedadnomina",
    "nomina_liquidacionnomina",
    "nomina_detalleliquidacion",
    "nomina_solicitudpago",
]


class Migration(migrations.Migration):
    dependencies = [
        ("nomina", "0001_initial"),
        ("documentos", "0001_initial"),
        ("core", "0002_auditoria"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)] + [migrations.RunSQL(*auditar(t)) for t in TABLAS_AUDITADAS]
