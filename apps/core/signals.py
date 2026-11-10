from django.contrib.auth.models import Group
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Rol, Usuario


@receiver(post_save, sender=Usuario)
def sincronizar_grupo_por_rol(sender, instance, **kwargs):
    """Cada usuario pertenece al grupo de permisos de su rol (los crea `inicializar_datos`)."""
    grupos_de_rol = Group.objects.filter(name__in=Rol.labels)
    grupo = grupos_de_rol.filter(name=instance.get_rol_display()).first()
    actuales = set(instance.groups.filter(pk__in=grupos_de_rol).values_list("pk", flat=True))
    deseados = {grupo.pk} if grupo else set()
    if actuales != deseados:
        instance.groups.remove(*(actuales - deseados))
        if grupo:
            instance.groups.add(grupo)
