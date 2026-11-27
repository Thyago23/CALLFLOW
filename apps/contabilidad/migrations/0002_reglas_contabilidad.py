"""
Reglas de la contabilidad que garantiza la base de datos (no se pueden saltar desde la aplicación ni el admin):

1. Cajas y bancos: la cuenta del plan es una cuenta de movimiento del activo; el saldo inicial no cambia
   cuando ya hay movimientos.
2. Movimientos de caja y bancos: no se borran ni se editan (solo se concilian o se anulan con motivo); la caja
   nunca queda en negativo; cliente, proveedor y traspaso son de la misma empresa; el traspaso cuadra.
3. Libro diario: el asiento se contabiliza solo si cuadra (debe = haber, al menos dos líneas, cuentas de movimiento
   activas, terceros de la misma empresa). Contabilizado ya no cambia (solo se anula). Las líneas solo se tocan en
   borrador. Solo el borrador se puede borrar.
4. Leads: el cliente en que se convierte es de la misma empresa. El historial de seguimientos no se edita.
5. Auditoría (core_registrocambio) de plan de cuentas, cajas y bancos, movimientos, asientos y líneas.
"""
from django.db import migrations

from apps.core.sql import auditar

SQL = r"""
-- Cuenta de movimiento = no tiene subcuentas.
CREATE OR REPLACE FUNCTION contabilidad_es_cuenta_de_movimiento(p_cuenta bigint) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM contabilidad_cuentacontable h, contabilidad_cuentacontable c
        WHERE c.id = p_cuenta AND h.codigo LIKE c.codigo || '.%'
    );
$$;

-- 1. Cajas y bancos ------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION contabilidad_cuentafin_reglas() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_tipo text;
BEGIN
    SELECT tipo INTO v_tipo FROM contabilidad_cuentacontable WHERE id = NEW.cuenta_contable_id;
    IF v_tipo <> 'ACTIVO' OR NOT contabilidad_es_cuenta_de_movimiento(NEW.cuenta_contable_id) THEN
        RAISE EXCEPTION 'La caja o banco debe ligarse a una cuenta de movimiento del activo (por ejemplo 1.1.01.01 Caja general)'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.empresa_id <> OLD.empresa_id OR NEW.tipo <> OLD.tipo
            OR NEW.saldo_inicial <> OLD.saldo_inicial OR NEW.fecha_saldo_inicial <> OLD.fecha_saldo_inicial)
            AND EXISTS (SELECT 1 FROM contabilidad_movimientofinanciero WHERE cuenta_id = NEW.id) THEN
        RAISE EXCEPTION 'La cuenta ya tiene movimientos: no se puede cambiar la empresa, el tipo ni el saldo inicial'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_cuentafin_reglas BEFORE INSERT OR UPDATE ON contabilidad_cuentafinanciera
    FOR EACH ROW EXECUTE FUNCTION contabilidad_cuentafin_reglas();

-- 2. Movimientos de caja y bancos ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION contabilidad_movfin_saldo(p_cuenta bigint, p_excluir bigint) RETURNS numeric
LANGUAGE sql STABLE AS $$
    SELECT c.saldo_inicial + COALESCE(SUM(CASE WHEN m.tipo IN ('INGRESO', 'DEPOSITO', 'TRANSF_REC', 'TRASP_ENT')
                                               THEN m.valor ELSE -m.valor END), 0)
    FROM contabilidad_cuentafinanciera c
    LEFT JOIN contabilidad_movimientofinanciero m ON m.cuenta_id = c.id AND NOT m.anulado AND m.id <> p_excluir
    WHERE c.id = p_cuenta
    GROUP BY c.saldo_inicial;
$$;

CREATE OR REPLACE FUNCTION contabilidad_movfin_reglas() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    c RECORD;
    par RECORD;
    v_entra boolean := NEW.tipo IN ('INGRESO', 'DEPOSITO', 'TRANSF_REC', 'TRASP_ENT');
    v_saldo numeric;
BEGIN
    -- Se bloquea la cuenta: dos personas no pueden sacar a la vez el mismo dinero de la caja.
    SELECT id, empresa_id, tipo, activa, fecha_saldo_inicial, nombre INTO c
    FROM contabilidad_cuentafinanciera WHERE id = NEW.cuenta_id FOR UPDATE;

    IF TG_OP = 'INSERT' THEN
        IF NOT c.activa THEN
            RAISE EXCEPTION 'La cuenta "%" está inactiva y no admite movimientos', c.nombre USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.anulado THEN
            RAISE EXCEPTION 'Un movimiento no se registra ya anulado' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.fecha < c.fecha_saldo_inicial THEN
            RAISE EXCEPTION 'La fecha es anterior al saldo inicial de "%" (%)', c.nombre, to_char(c.fecha_saldo_inicial, 'DD/MM/YYYY')
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.cliente_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM contabilidad_cliente WHERE id = NEW.cliente_id AND empresa_id = c.empresa_id) THEN
            RAISE EXCEPTION 'El cliente es de otra empresa' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.proveedor_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM contabilidad_proveedor WHERE id = NEW.proveedor_id AND empresa_id = c.empresa_id) THEN
            RAISE EXCEPTION 'El proveedor es de otra empresa' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.tipo = 'TRASP_SAL' THEN
            SELECT m.tipo, m.valor, m.fecha, m.cuenta_id, f.empresa_id, m.anulado INTO par
            FROM contabilidad_movimientofinanciero m JOIN contabilidad_cuentafinanciera f ON f.id = m.cuenta_id
            WHERE m.id = NEW.traspaso_id;
            IF par.tipo <> 'TRASP_ENT' OR par.valor <> NEW.valor OR par.fecha <> NEW.fecha OR par.anulado
                    OR par.cuenta_id = NEW.cuenta_id OR par.empresa_id <> c.empresa_id THEN
                RAISE EXCEPTION 'El traspaso no cuadra: la entrada debe ser en otra cuenta de la misma empresa, por el mismo valor y fecha'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        IF c.tipo = 'CAJA' AND NOT v_entra THEN
            v_saldo := contabilidad_movfin_saldo(c.id, -1);
            IF NEW.valor > v_saldo THEN
                RAISE EXCEPTION 'Saldo insuficiente en "%": tiene %, se quiere sacar %', c.nombre, v_saldo, NEW.valor
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: lo registrado no cambia; solo la conciliación, las observaciones, el soporte (si no tenía) y la anulación.
    IF OLD.anulado THEN
        RAISE EXCEPTION 'El movimiento está anulado: ya no se modifica' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.cuenta_id, NEW.fecha, NEW.tipo, NEW.concepto, NEW.cliente_id, NEW.proveedor_id, NEW.tercero, NEW.referencia,
            NEW.valor, NEW.traspaso_id, NEW.registrado_por_id, NEW.creado_en)
            IS DISTINCT FROM
            (OLD.cuenta_id, OLD.fecha, OLD.tipo, OLD.concepto, OLD.cliente_id, OLD.proveedor_id, OLD.tercero, OLD.referencia,
             OLD.valor, OLD.traspaso_id, OLD.registrado_por_id, OLD.creado_en) THEN
        RAISE EXCEPTION 'Los movimientos de caja y bancos no se modifican: anúlelo y regístrelo de nuevo'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.soporte <> '' AND NEW.soporte IS DISTINCT FROM OLD.soporte THEN
        RAISE EXCEPTION 'El soporte del movimiento ya no se reemplaza' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.anulado AND c.tipo = 'CAJA' AND v_entra THEN  -- anular un ingreso que ya se gastó
        v_saldo := contabilidad_movfin_saldo(c.id, NEW.id);
        IF v_saldo < 0 THEN
            RAISE EXCEPTION 'No se puede anular: la caja "%" quedaría en negativo (%)', c.nombre, v_saldo
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_movfin_reglas BEFORE INSERT OR UPDATE ON contabilidad_movimientofinanciero
    FOR EACH ROW EXECUTE FUNCTION contabilidad_movfin_reglas();

-- Un traspaso se anula completo: sus dos lados a la vez.
CREATE OR REPLACE FUNCTION contabilidad_movfin_traspaso_anulado() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_par bigint;
BEGIN
    IF NEW.anulado AND NOT OLD.anulado AND NEW.tipo IN ('TRASP_ENT', 'TRASP_SAL') THEN
        IF NEW.tipo = 'TRASP_SAL' THEN
            v_par := NEW.traspaso_id;
        ELSE
            SELECT id INTO v_par FROM contabilidad_movimientofinanciero WHERE traspaso_id = NEW.id;
        END IF;
        IF EXISTS (SELECT 1 FROM contabilidad_movimientofinanciero WHERE id = v_par AND NOT anulado) THEN
            RAISE EXCEPTION 'Anule los dos lados del traspaso a la vez' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER trg_movfin_traspaso_anulado AFTER UPDATE ON contabilidad_movimientofinanciero
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION contabilidad_movfin_traspaso_anulado();

CREATE OR REPLACE FUNCTION contabilidad_no_borrar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = 'check_violation';
END;
$$;
CREATE TRIGGER trg_movfin_no_borrar BEFORE DELETE ON contabilidad_movimientofinanciero
    FOR EACH ROW EXECUTE FUNCTION contabilidad_no_borrar('Los movimientos de caja y bancos no se borran: anúlelos');

-- 3. Libro diario ------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION contabilidad_asiento_reglas() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_lineas int;
    v_debe numeric;
    v_haber numeric;
    v_cuenta text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.estado <> 'BORRADOR' THEN
            RAISE EXCEPTION 'Solo se borran los asientos en borrador; un asiento contabilizado se anula'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.estado <> 'BORRADOR' THEN
            RAISE EXCEPTION 'El asiento nace en borrador' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.empresa_id <> OLD.empresa_id THEN
        RAISE EXCEPTION 'El asiento no cambia de empresa' USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.estado = 'ANULADO' THEN
        RAISE EXCEPTION 'El asiento está anulado: ya no se modifica' USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.estado = 'CONTABILIZADO' THEN
        IF NEW.estado <> 'ANULADO' OR (NEW.numero, NEW.fecha, NEW.tipo, NEW.concepto, NEW.documento,
                NEW.contabilizado_por_id, NEW.contabilizado_en, NEW.registrado_por_id)
                IS DISTINCT FROM (OLD.numero, OLD.fecha, OLD.tipo, OLD.concepto, OLD.documento,
                OLD.contabilizado_por_id, OLD.contabilizado_en, OLD.registrado_por_id) THEN
            RAISE EXCEPTION 'El asiento ya está contabilizado: no se modifica, solo se puede anular'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- Desde borrador
    IF NEW.estado = 'ANULADO' THEN
        RAISE EXCEPTION 'Un borrador no se anula: se borra' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.estado = 'CONTABILIZADO' THEN
        SELECT count(*), COALESCE(SUM(debe), 0), COALESCE(SUM(haber), 0) INTO v_lineas, v_debe, v_haber
        FROM contabilidad_lineaasiento WHERE asiento_id = NEW.id;
        IF v_lineas < 2 THEN
            RAISE EXCEPTION 'El asiento necesita al menos dos líneas' USING ERRCODE = 'check_violation';
        END IF;
        IF v_debe <> v_haber THEN
            RAISE EXCEPTION 'El asiento no cuadra: debe % y haber % (diferencia %)', v_debe, v_haber, v_debe - v_haber
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT c.codigo || ' ' || c.nombre INTO v_cuenta
        FROM contabilidad_lineaasiento l JOIN contabilidad_cuentacontable c ON c.id = l.cuenta_id
        WHERE l.asiento_id = NEW.id AND (NOT c.activa OR NOT contabilidad_es_cuenta_de_movimiento(c.id))
        LIMIT 1;
        IF v_cuenta IS NOT NULL THEN
            RAISE EXCEPTION 'La cuenta % no admite movimientos (está inactiva o tiene subcuentas)', v_cuenta
                USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (
            SELECT 1 FROM contabilidad_lineaasiento l
            LEFT JOIN contabilidad_cliente cl ON cl.id = l.cliente_id
            LEFT JOIN contabilidad_proveedor pr ON pr.id = l.proveedor_id
            WHERE l.asiento_id = NEW.id AND (cl.empresa_id <> NEW.empresa_id OR pr.empresa_id <> NEW.empresa_id)
        ) THEN
            RAISE EXCEPTION 'Hay un cliente o proveedor de otra empresa en el asiento' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_asiento_reglas BEFORE INSERT OR UPDATE OR DELETE ON contabilidad_asiento
    FOR EACH ROW EXECUTE FUNCTION contabilidad_asiento_reglas();

CREATE OR REPLACE FUNCTION contabilidad_linea_solo_borrador() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_estado text;
BEGIN
    SELECT estado INTO v_estado FROM contabilidad_asiento
    WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.asiento_id ELSE NEW.asiento_id END FOR UPDATE;
    IF v_estado IS DISTINCT FROM 'BORRADOR' OR (TG_OP = 'UPDATE' AND NEW.asiento_id <> OLD.asiento_id) THEN
        RAISE EXCEPTION 'Las líneas solo se cambian mientras el asiento está en borrador' USING ERRCODE = 'check_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
CREATE TRIGGER trg_linea_solo_borrador BEFORE INSERT OR UPDATE OR DELETE ON contabilidad_lineaasiento
    FOR EACH ROW EXECUTE FUNCTION contabilidad_linea_solo_borrador();

-- 4. Leads -------------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION contabilidad_lead_reglas() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.cliente_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM contabilidad_cliente WHERE id = NEW.cliente_id AND empresa_id = NEW.empresa_id) THEN
        RAISE EXCEPTION 'El cliente del lead es de otra empresa' USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.estado = 'CONVERTIDO' AND (NEW.estado <> 'CONVERTIDO' OR NEW.cliente_id <> OLD.cliente_id) THEN
        RAISE EXCEPTION 'El lead ya se convirtió en cliente' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_lead_reglas BEFORE INSERT OR UPDATE ON contabilidad_lead
    FOR EACH ROW EXECUTE FUNCTION contabilidad_lead_reglas();

CREATE TRIGGER trg_seguimiento_inmutable BEFORE UPDATE OR DELETE ON contabilidad_seguimientolead
    FOR EACH ROW EXECUTE FUNCTION contabilidad_no_borrar('El historial de seguimientos no se modifica');
"""

REVERSO = r"""
DROP TRIGGER IF EXISTS trg_seguimiento_inmutable ON contabilidad_seguimientolead;
DROP TRIGGER IF EXISTS trg_lead_reglas ON contabilidad_lead;
DROP TRIGGER IF EXISTS trg_linea_solo_borrador ON contabilidad_lineaasiento;
DROP TRIGGER IF EXISTS trg_asiento_reglas ON contabilidad_asiento;
DROP TRIGGER IF EXISTS trg_movfin_no_borrar ON contabilidad_movimientofinanciero;
DROP TRIGGER IF EXISTS trg_movfin_traspaso_anulado ON contabilidad_movimientofinanciero;
DROP TRIGGER IF EXISTS trg_movfin_reglas ON contabilidad_movimientofinanciero;
DROP TRIGGER IF EXISTS trg_cuentafin_reglas ON contabilidad_cuentafinanciera;
DROP FUNCTION IF EXISTS contabilidad_lead_reglas();
DROP FUNCTION IF EXISTS contabilidad_linea_solo_borrador();
DROP FUNCTION IF EXISTS contabilidad_asiento_reglas();
DROP FUNCTION IF EXISTS contabilidad_no_borrar();
DROP FUNCTION IF EXISTS contabilidad_movfin_traspaso_anulado();
DROP FUNCTION IF EXISTS contabilidad_movfin_reglas();
DROP FUNCTION IF EXISTS contabilidad_movfin_saldo(bigint, bigint);
DROP FUNCTION IF EXISTS contabilidad_cuentafin_reglas();
DROP FUNCTION IF EXISTS contabilidad_es_cuenta_de_movimiento(bigint);
"""

TABLAS_AUDITADAS = [
    "contabilidad_cuentacontable",
    "contabilidad_cuentafinanciera",
    "contabilidad_movimientofinanciero",
    "contabilidad_asiento",
    "contabilidad_lineaasiento",
]


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0001_initial"),
        ("core", "0002_auditoria"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)] + [migrations.RunSQL(*auditar(t)) for t in TABLAS_AUDITADAS]
