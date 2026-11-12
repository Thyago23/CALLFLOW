"""
Reglas de la mensajería interna que garantiza la base de datos:
- Solo pueden participar cuentas activas con rol de TI, administrador o supervisor (ENCARGADO_CIERRE):
  un asesor de ventas no puede ser parte de una conversación, aunque se intente con SQL directo.
- Una conversación directa tiene como mucho dos participantes.
- Solo escribe quien participa (activo) en la conversación y tiene uno de esos roles.
- Un mensaje no se edita ni se borra: solo se marca eliminado (sin texto ni adjunto), una vez.
- Cada mensaje nuevo actualiza la última actividad de la conversación (orden de la bandeja).
"""
from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION mensajes_rol_permitido(p_usuario bigint) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM core_usuario WHERE id = p_usuario AND is_active
                   AND rol IN ('ADMINISTRADOR', 'ENCARGADO_CIERRE', 'TI'));
$$;

CREATE OR REPLACE FUNCTION mensajes_participante_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_tipo varchar;
    v_cantidad integer;
BEGIN
    IF NEW.activo AND NOT mensajes_rol_permitido(NEW.usuario_id) THEN
        RAISE EXCEPTION 'La mensajería interna es solo para TI, administradores y supervisores'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT tipo INTO v_tipo FROM mensajes_conversacion WHERE id = NEW.conversacion_id;
    IF TG_OP = 'INSERT' AND v_tipo = 'DIRECTA' THEN
        SELECT count(*) INTO v_cantidad FROM mensajes_participante WHERE conversacion_id = NEW.conversacion_id;
        IF v_cantidad >= 2 THEN
            RAISE EXCEPTION 'Una conversación directa es entre dos personas' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_mensajes_participante_validar
    BEFORE INSERT OR UPDATE ON mensajes_participante
    FOR EACH ROW EXECUTE FUNCTION mensajes_participante_validar();

CREATE OR REPLACE FUNCTION mensajes_mensaje_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Los mensajes no se borran; quien los escribió puede eliminarlos' USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.eliminado THEN
            RAISE EXCEPTION 'Un mensaje nuevo no puede nacer eliminado' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.autor_id IS NULL OR NOT mensajes_rol_permitido(NEW.autor_id) OR NOT EXISTS (
            SELECT 1 FROM mensajes_participante
            WHERE conversacion_id = NEW.conversacion_id AND usuario_id = NEW.autor_id AND activo) THEN
            RAISE EXCEPTION 'Solo los participantes de la conversación (TI, administradores y supervisores) pueden escribir en ella'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    -- UPDATE: lo único permitido es eliminarlo (una vez).
    IF OLD.eliminado OR NOT NEW.eliminado
       OR (NEW.conversacion_id, NEW.autor_id, NEW.creado_en) IS DISTINCT FROM (OLD.conversacion_id, OLD.autor_id, OLD.creado_en) THEN
        RAISE EXCEPTION 'Los mensajes no se editan; quien los escribió puede eliminarlos' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_mensajes_mensaje_validar
    BEFORE INSERT OR UPDATE OR DELETE ON mensajes_mensaje
    FOR EACH ROW EXECUTE FUNCTION mensajes_mensaje_validar();

CREATE OR REPLACE FUNCTION mensajes_mensaje_actividad() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE mensajes_conversacion SET ultimo_mensaje_en = NEW.creado_en, actualizado_en = now()
    WHERE id = NEW.conversacion_id;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_mensajes_mensaje_actividad
    AFTER INSERT ON mensajes_mensaje
    FOR EACH ROW EXECUTE FUNCTION mensajes_mensaje_actividad();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_mensajes_mensaje_actividad ON mensajes_mensaje;
DROP FUNCTION IF EXISTS mensajes_mensaje_actividad();
DROP TRIGGER IF EXISTS trg_mensajes_mensaje_validar ON mensajes_mensaje;
DROP FUNCTION IF EXISTS mensajes_mensaje_validar();
DROP TRIGGER IF EXISTS trg_mensajes_participante_validar ON mensajes_participante;
DROP FUNCTION IF EXISTS mensajes_participante_validar();
DROP FUNCTION IF EXISTS mensajes_rol_permitido(bigint);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("mensajes", "0001_initial"),
        ("core", "0015_roles_con_ti"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
