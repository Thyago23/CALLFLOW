from django.db import migrations

from apps.core.codigos import base_usuario, siguiente_usuario


def usuarios_a_nombre_apellido(apps, schema_editor):
    """Los usuarios existentes pasan a ingresar con nombre.apellido (su contraseña no cambia)."""
    Usuario = apps.get_model("core", "Usuario")
    usados = set()
    for usuario in Usuario.objects.order_by("date_joined", "pk"):
        nuevo = siguiente_usuario(base_usuario(usuario.first_name or usuario.username, usuario.last_name), usados)
        usados.add(nuevo)
        if usuario.username != nuevo:
            usuario.username = nuevo
            usuario.save(update_fields=["username"])


class Migration(migrations.Migration):
    dependencies = [("core", "0005_codigo_usuario")]
    operations = [migrations.RunPython(usuarios_a_nombre_apellido, migrations.RunPython.noop)]
