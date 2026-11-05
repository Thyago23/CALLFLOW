from django.db import migrations

from apps.core.sql import auditar

FUNCION_AUDITORIA = """
CREATE OR REPLACE FUNCTION core_auditar_cambios() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_usuario bigint;
    v_anterior jsonb;
    v_nuevo jsonb;
BEGIN
    -- El middleware de Django fija este valor en cada petición; NULL si el cambio viene de otro origen.
    v_usuario := NULLIF(current_setting('callflow.usuario_id', true), '')::bigint;

    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        v_anterior := to_jsonb(OLD) - 'password';
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        v_nuevo := to_jsonb(NEW) - 'password';
    END IF;

    -- No registrar actualizaciones que no cambian nada (aparte de la fecha de actualización).
    IF TG_OP = 'UPDATE' AND (v_anterior - 'actualizado_en') = (v_nuevo - 'actualizado_en') THEN
        RETURN NULL;
    END IF;

    INSERT INTO core_registrocambio (tabla, registro_id, accion, datos_anteriores, datos_nuevos, usuario_id, fecha)
    VALUES (TG_TABLE_NAME, COALESCE(v_nuevo, v_anterior) ->> 'id', TG_OP, v_anterior, v_nuevo, v_usuario, now());
    RETURN NULL;
END;
$$;

-- La bitácora es de solo agregar: nadie puede modificarla ni borrarla.
CREATE OR REPLACE FUNCTION core_bitacora_inmutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'El registro de cambios no se puede modificar ni eliminar'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER trg_bitacora_inmutable
    BEFORE UPDATE OR DELETE ON core_registrocambio
    FOR EACH ROW EXECUTE FUNCTION core_bitacora_inmutable();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_bitacora_inmutable ON core_registrocambio;
DROP FUNCTION IF EXISTS core_bitacora_inmutable();
DROP FUNCTION IF EXISTS core_auditar_cambios() CASCADE;
"""

# Cambios de rol o de estado de los usuarios también quedan auditados (sin la contraseña).
USUARIO_SQL, USUARIO_REVERSO = auditar("core_usuario")


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.RunSQL(FUNCION_AUDITORIA, REVERSO),
        migrations.RunSQL(USUARIO_SQL, USUARIO_REVERSO),
    ]
