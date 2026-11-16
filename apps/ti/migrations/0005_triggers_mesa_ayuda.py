"""La conversación de los reportes no se edita ni se borra; auditoría de reportes, licencias y planes."""
from django.db import migrations

from apps.core.sql import auditar

COMENTARIO_INMUTABLE = """
CREATE OR REPLACE FUNCTION ti_comentario_inmutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'El seguimiento de un reporte no se modifica ni se elimina: escriba un mensaje nuevo.'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_ti_comentario_inmutable BEFORE UPDATE OR DELETE ON ti_comentarioticket
    FOR EACH ROW EXECUTE FUNCTION ti_comentario_inmutable();
"""
COMENTARIO_INMUTABLE_REVERSO = """
DROP TRIGGER IF EXISTS trg_ti_comentario_inmutable ON ti_comentarioticket;
DROP FUNCTION IF EXISTS ti_comentario_inmutable();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ti", "0004_mesa_ayuda_y_avisos"),
    ]

    operations = [
        migrations.RunSQL(COMENTARIO_INMUTABLE, COMENTARIO_INMUTABLE_REVERSO),
        migrations.RunSQL(*auditar("ti_ticket")),
        migrations.RunSQL(*auditar("ti_licencia")),
        migrations.RunSQL(*auditar("ti_planmantenimiento")),
    ]
