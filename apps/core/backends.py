import re

from django.contrib.auth.backends import ModelBackend

from .codigos import normalizar_usuario
from .models import Usuario


class UsuarioNombreBackend(ModelBackend):
    """
    Ingreso con nombre.apellido + código de acceso.

    Acepta el usuario escrito de varias formas ('Lucía Mora', 'lucia mora', 'lucia.mora').
    Si dos personas comparten nombre (lucia.mora y lucia.mora2) y se escribe solo el nombre,
    se identifica a la persona por su código, que es aleatorio y distinto para cada una.
    Las iniciales del código se aceptan en minúsculas (lm48207153).
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        # Cuenta bloqueada por intentos fallidos: ni siquiera se revisa la contraseña (apps.core.limite_peticiones).
        from .limite_peticiones import ingreso_bloqueado

        if ingreso_bloqueado(username):
            return None
        usuario_normalizado = normalizar_usuario(username)
        if not usuario_normalizado:
            return None

        if re.search(r"\d$", usuario_normalizado):
            candidatos = Usuario.objects.filter(username=usuario_normalizado)
        else:
            candidatos = Usuario.objects.filter(username__regex=rf"^{re.escape(usuario_normalizado)}[0-9]*$")
        candidatos = list(candidatos)
        if not candidatos:
            Usuario().set_password(password)  # mismo costo de tiempo que un usuario existente
            return None

        variantes = [password]
        if len(password) > 2 and password[:2].isalpha():
            variantes.append(password[:2].upper() + password[2:])
        for usuario in candidatos:
            if any(usuario.check_password(v) for v in variantes) and self.user_can_authenticate(usuario):
                return usuario
        return None
