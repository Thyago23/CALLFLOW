"""Auditoría de chips y reposiciones; el libro de reposiciones no se edita ni se borra."""
from django.db import migrations

from apps.core.sql import auditar

INMUTABLE = """
CREATE OR REPLACE FUNCTION chips_reposicion_inmutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Las reposiciones de chips no se modifican ni se eliminan: registre una nueva.'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_chips_reposicion_inmutable BEFORE UPDATE OR DELETE ON chips_reposicion
    FOR EACH ROW EXECUTE FUNCTION chips_reposicion_inmutable();
"""
INMUTABLE_REVERSO = """
DROP TRIGGER IF EXISTS trg_chips_reposicion_inmutable ON chips_reposicion;
DROP FUNCTION IF EXISTS chips_reposicion_inmutable();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("chips", "0001_initial"),
        ("core", "0002_auditoria"),
    ]

    operations = [
        migrations.RunSQL(INMUTABLE, INMUTABLE_REVERSO),
        migrations.RunSQL(*auditar("chips_chip")),
        migrations.RunSQL(*auditar("chips_reposicion")),
    ]
