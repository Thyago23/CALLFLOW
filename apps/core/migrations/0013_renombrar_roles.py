"""
Nombres de los roles según la empresa: «Vendedor» es «Asesor de ventas» y «Encargado de cierre» es «Supervisor».

Los códigos internos (VENDEDOR, ENCARGADO_CIERRE) no cambian. Se renombran los grupos de permisos (llevan el
nombre visible del rol) y los cargos, para que cada usuario y cada empleado conserve lo que tenía.
"""
from django.db import migrations

GRUPOS = [("Vendedor", "Asesor de ventas"), ("Encargado de cierre", "Supervisor")]
CARGOS = [("Vendedor telefónico", "Asesor de ventas"), ("Encargado de cierre", "Supervisor")]


def renombrar_grupos(Group, pares):
    for viejo, nuevo in pares:
        anterior = Group.objects.filter(name=viejo).first()
        if not anterior:
            continue
        actual = Group.objects.filter(name=nuevo).first()
        if actual:  # ya existía con el nombre nuevo: se pasan los usuarios y se borra el viejo
            for usuario in anterior.user_set.all():
                usuario.groups.add(actual)
            anterior.delete()
        else:
            anterior.name = nuevo
            anterior.save(update_fields=["name"])


def renombrar_cargos(Cargo, Empleado, pares):
    for viejo, nuevo in pares:
        anterior = Cargo.objects.filter(nombre=viejo).first()
        if not anterior:
            continue
        actual = Cargo.objects.filter(nombre=nuevo).first()
        if actual:  # ya existía: los empleados pasan al cargo nuevo y el viejo queda inactivo
            Empleado.objects.filter(cargo=anterior).update(cargo=actual)
            anterior.activo = False
            anterior.save(update_fields=["activo"])
        else:
            anterior.nombre = nuevo
            anterior.save(update_fields=["nombre"])


def adelante(apps, schema_editor):
    renombrar_grupos(apps.get_model("auth", "Group"), GRUPOS)
    renombrar_cargos(apps.get_model("personal", "Cargo"), apps.get_model("personal", "Empleado"), CARGOS)


def atras(apps, schema_editor):
    renombrar_grupos(apps.get_model("auth", "Group"), [(n, v) for v, n in GRUPOS])
    renombrar_cargos(apps.get_model("personal", "Cargo"), apps.get_model("personal", "Empleado"), [(n, v) for v, n in CARGOS])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_alter_usuario_rol"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("personal", "0003_observaciones"),
    ]

    operations = [migrations.RunPython(adelante, atras)]
