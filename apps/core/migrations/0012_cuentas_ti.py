"""
Rol «Tecnología (TI)» y las dos cuentas del departamento de TI quemadas en el sistema.

- Las cuentas (santiago.cedeno y nicolas.correa) se crean aquí, sin contraseña utilizable: el código de
  acceso se genera con `python manage.py credenciales_ti`.
- Un trigger impide borrarlas, desactivarlas o cambiarles el usuario, el rol o la marca de protegida,
  aunque se intente con SQL directo. Sí se puede cambiar su contraseña, nombre, foto y tema.
"""
from django.contrib.auth.hashers import make_password
from django.db import migrations, models
from django.utils import timezone

CUENTAS = [
    ("santiago.cedeno", "Santiago", "Cedeño"),
    ("nicolas.correa", "Nicolas", "Correa"),
]

PROTEGER = """
CREATE OR REPLACE FUNCTION core_proteger_cuentas_ti() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.protegida THEN
            RAISE EXCEPTION 'La cuenta % es una cuenta fija del departamento de TI: no se puede eliminar.', OLD.username
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.protegida AND (NEW.username <> OLD.username OR NEW.rol <> OLD.rol OR NOT NEW.protegida OR NOT NEW.is_active) THEN
        RAISE EXCEPTION 'La cuenta % es una cuenta fija del departamento de TI: no se puede desactivar ni cambiarle el usuario o el rol.', OLD.username
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_usuario_cuentas_ti BEFORE UPDATE OR DELETE ON core_usuario
    FOR EACH ROW EXECUTE FUNCTION core_proteger_cuentas_ti();
"""
PROTEGER_REVERSO = """
DROP TRIGGER IF EXISTS trg_usuario_cuentas_ti ON core_usuario;
DROP FUNCTION IF EXISTS core_proteger_cuentas_ti();
"""


def crear_cuentas(apps, schema_editor):
    Usuario = apps.get_model("core", "Usuario")
    for usuario, nombres, apellidos in CUENTAS:
        if Usuario.objects.filter(username=usuario).exists():
            Usuario.objects.filter(username=usuario).update(rol="TI", protegida=True, is_active=True)
            continue
        Usuario.objects.create(
            username=usuario, first_name=nombres, last_name=apellidos, rol="TI", protegida=True,
            password=make_password(None), is_active=True, is_staff=False, is_superuser=False, date_joined=timezone.now(),
        )


def quitar_proteccion(apps, schema_editor):
    # Al revertir, las cuentas quedan como usuarios normales (no se borran sus registros).
    apps.get_model("core", "Usuario").objects.filter(username__in=[c[0] for c in CUENTAS]).update(protegida=False)


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("core", "0011_ayuda_entre_asesores"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="usuario",
            name="usuario_rol_valido",
        ),
        migrations.AddField(
            model_name="usuario",
            name="protegida",
            field=models.BooleanField(default=False, editable=False, help_text="Cuenta fija del sistema (TI): no se puede borrar, desactivar ni cambiarle el usuario o el rol.", verbose_name="cuenta protegida"),
        ),
        migrations.AlterField(
            model_name="usuario",
            name="rol",
            field=models.CharField(choices=[("ADMINISTRADOR", "Administrador"), ("VENDEDOR", "Vendedor"), ("ENCARGADO_CIERRE", "Encargado de cierre"), ("TI", "Tecnología (TI)")], default="VENDEDOR", max_length=20),
        ),
        migrations.AddConstraint(
            model_name="usuario",
            constraint=models.CheckConstraint(condition=models.Q(("rol__in", ["ADMINISTRADOR", "VENDEDOR", "ENCARGADO_CIERRE", "TI"])), name="usuario_rol_valido"),
        ),
        # Valor por defecto también en la base, para inserciones por SQL directo.
        migrations.RunSQL("ALTER TABLE core_usuario ALTER COLUMN protegida SET DEFAULT false;",
                          "ALTER TABLE core_usuario ALTER COLUMN protegida DROP DEFAULT;"),
        migrations.RunPython(crear_cuentas, quitar_proteccion),
        migrations.RunSQL(PROTEGER, PROTEGER_REVERSO),
    ]
