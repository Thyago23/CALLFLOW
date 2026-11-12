"""Auditoría de los equipos y su hoja de vida; la hoja de vida no se edita ni se borra."""
from django.db import migrations

from apps.core.sql import auditar

INMUTABLE = """
CREATE OR REPLACE FUNCTION ti_evento_inmutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'La hoja de vida de los equipos no se modifica ni se elimina: registre un evento nuevo.'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_ti_evento_inmutable BEFORE UPDATE OR DELETE ON ti_eventoequipo
    FOR EACH ROW EXECUTE FUNCTION ti_evento_inmutable();
"""
INMUTABLE_REVERSO = """
DROP TRIGGER IF EXISTS trg_ti_evento_inmutable ON ti_eventoequipo;
DROP FUNCTION IF EXISTS ti_evento_inmutable();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ti", "0001_initial"),
        ("core", "0012_cuentas_ti"),
    ]

    operations = [
        migrations.RunSQL(INMUTABLE, INMUTABLE_REVERSO),
        migrations.RunSQL(*auditar("ti_equipo")),
        migrations.RunSQL(*auditar("ti_eventoequipo")),
    ]
