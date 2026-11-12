"""
Edición de mensajes con historial, garantizada por la base de datos:
- Un mensaje (no eliminado) solo puede cambiar su texto, y al hacerlo debe quedar marcado editado_en.
  No cambian la conversación, el autor, la fecha ni el adjunto.
- Cada edición guarda el texto anterior en mensajes_mensajeedicion (lo escribe este trigger, no la aplicación).
- Eliminar sigue igual: una sola vez, sin texto ni adjunto. Borrar filas sigue prohibido.
- El historial de ediciones tampoco se modifica ni se borra.
"""
from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION mensajes_mensaje_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Los mensajes no se borran; quien los escribió puede eliminarlos' USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.eliminado OR NEW.editado_en IS NOT NULL THEN
            RAISE EXCEPTION 'Un mensaje nuevo no puede nacer eliminado ni editado' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.autor_id IS NULL OR NOT mensajes_rol_permitido(NEW.autor_id) OR NOT EXISTS (
            SELECT 1 FROM mensajes_participante
            WHERE conversacion_id = NEW.conversacion_id AND usuario_id = NEW.autor_id AND activo) THEN
            RAISE EXCEPTION 'Solo los participantes de la conversación (TI, administradores y supervisores) pueden escribir en ella'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.eliminado THEN
        RAISE EXCEPTION 'El mensaje ya está eliminado' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.conversacion_id, NEW.autor_id, NEW.creado_en) IS DISTINCT FROM (OLD.conversacion_id, OLD.autor_id, OLD.creado_en) THEN
        RAISE EXCEPTION 'Los mensajes no cambian de conversación, autor ni fecha' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.eliminado THEN
        RETURN NEW;  -- eliminar: la restricción mensaje_eliminado_sin_contenido exige que quede vacío
    END IF;
    -- Editar: solo el texto, con la marca de edición al día; el adjunto no cambia.
    IF (NEW.adjunto, NEW.adjunto_nombre, NEW.adjunto_tipo) IS DISTINCT FROM (OLD.adjunto, OLD.adjunto_nombre, OLD.adjunto_tipo) THEN
        RAISE EXCEPTION 'El adjunto de un mensaje no se cambia; elimine el mensaje y envíe otro' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.texto IS DISTINCT FROM OLD.texto AND (NEW.editado_en IS NULL OR NEW.editado_en IS NOT DISTINCT FROM OLD.editado_en) THEN
        RAISE EXCEPTION 'Al editar un mensaje debe quedar marcado como editado' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION mensajes_mensaje_historial() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT NEW.eliminado AND NEW.texto IS DISTINCT FROM OLD.texto THEN
        INSERT INTO mensajes_mensajeedicion (mensaje_id, texto_anterior, editado_en) VALUES (OLD.id, OLD.texto, now());
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_mensajes_mensaje_historial
    AFTER UPDATE ON mensajes_mensaje
    FOR EACH ROW EXECUTE FUNCTION mensajes_mensaje_historial();

CREATE OR REPLACE FUNCTION mensajes_edicion_inmutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'El historial de ediciones no se modifica ni se borra' USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER trg_mensajes_edicion_inmutable
    BEFORE UPDATE OR DELETE ON mensajes_mensajeedicion
    FOR EACH ROW EXECUTE FUNCTION mensajes_edicion_inmutable();
"""

REVERSO = """
DROP TRIGGER IF EXISTS trg_mensajes_edicion_inmutable ON mensajes_mensajeedicion;
DROP FUNCTION IF EXISTS mensajes_edicion_inmutable();
DROP TRIGGER IF EXISTS trg_mensajes_mensaje_historial ON mensajes_mensaje;
DROP FUNCTION IF EXISTS mensajes_mensaje_historial();
"""


class Migration(migrations.Migration):
    dependencies = [("mensajes", "0004_edicion")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
