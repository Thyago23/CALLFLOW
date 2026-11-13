"""Formulario de filtros: muestra solo los que aplican a cada reporte (SCRUM-62)."""
from datetime import date

from django import forms
from django.utils import timezone

from apps.core.models import Rol

ETIQUETAS = {"desde": "Desde", "hasta": "Hasta", "mes": "Mes", "empleado": "Empleado", "vendedor": "Vendedor",
             "equipo": "Equipo", "estado": "Estado", "cargo": "Cargo", "categoria": "Categoría", "base": "Base comercial",
             "tipo_equipo": "Tipo de equipo", "usuario": "Usuario", "ciudad": "Ciudad", "tarjeta": "Categoría de tarjeta",
             "llamado_desde": "Llamados desde", "llamado_hasta": "Llamados hasta"}


def _mes_actual() -> str:
    return f"{timezone.localdate():%Y-%m}"


class FiltrosForm(forms.Form):
    def __init__(self, data, reporte, usuario):
        super().__init__(data)
        self.reporte, self.usuario = reporte, usuario
        hoy = timezone.localdate()
        for nombre in reporte.filtros:
            if nombre == "vendedor" and usuario.rol == Rol.VENDEDOR:
                continue  # el vendedor solo ve sus propios datos (ver limpiar)
            requerido = nombre in reporte.obligatorios
            self.fields[nombre] = self._campo(nombre, requerido, hoy)
            self.fields[nombre].label = ETIQUETAS[nombre]

    def _campo(self, nombre, requerido, hoy):
        from apps.inventario.models import CategoriaMaterial
        from apps.personal.models import Cargo, Empleado, EsquemaComision
        from apps.ventas.servicios import vendedores_con_hoja

        fecha = forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d")
        lista = {"class": "form-select"}
        if nombre == "desde":
            return forms.DateField(required=False, widget=fecha, initial=hoy.replace(day=1))
        if nombre == "hasta":
            return forms.DateField(required=False, widget=fecha, initial=hoy)
        if nombre == "mes":
            return forms.CharField(required=False, initial=_mes_actual(),
                                   widget=forms.TextInput(attrs={"type": "month", "class": "form-control"}))
        if nombre == "empleado":
            return forms.ModelChoiceField(Empleado.objects.order_by("apellidos", "nombres"), required=requerido,
                                          empty_label="Elija un empleado" if requerido else "Todos los empleados",
                                          widget=forms.Select(attrs=lista))
        if nombre == "vendedor":
            return forms.ModelChoiceField(vendedores_con_hoja().order_by("apellidos", "nombres"), required=requerido,
                                          empty_label="Elija un vendedor" if requerido else "Todos los vendedores",
                                          widget=forms.Select(attrs=lista))
        if nombre == "equipo":
            return forms.ModelChoiceField(EsquemaComision.objects.filter(activo=True).order_by("nombre"), required=False,
                                          empty_label="Todos los equipos", widget=forms.Select(attrs=lista))
        if nombre == "cargo":
            return forms.ModelChoiceField(Cargo.objects.order_by("nombre"), required=False, empty_label="Todos los cargos",
                                          widget=forms.Select(attrs=lista))
        if nombre == "categoria":
            return forms.ModelChoiceField(CategoriaMaterial.objects.order_by("nombre"), required=False,
                                          empty_label="Todas las categorías", widget=forms.Select(attrs=lista))
        if nombre == "base":
            from apps.ventas.models import BaseComercial

            return forms.ModelChoiceField(BaseComercial.objects.order_by("-creado_en"), required=False, empty_label="Todas las bases",
                                          widget=forms.Select(attrs=lista))
        if nombre == "tipo_equipo":
            from apps.ti.models import TipoEquipo

            return forms.ChoiceField(choices=[("", "Todos los tipos")] + list(TipoEquipo.choices), required=False,
                                     widget=forms.Select(attrs=lista))
        if nombre == "usuario":
            from apps.core.models import Usuario

            return forms.ModelChoiceField(Usuario.objects.order_by("first_name", "last_name"), required=False,
                                          empty_label="Todos los usuarios", widget=forms.Select(attrs=lista))
        if nombre in ("ciudad", "tarjeta"):
            from apps.ventas.servicios import opciones_contactos

            opciones = opciones_contactos("ciudad" if nombre == "ciudad" else "categoria_tarjeta")
            vacio = "Todas las ciudades" if nombre == "ciudad" else "Todas las categorías"
            return forms.ChoiceField(choices=[("", vacio)] + [(o, o) for o in opciones], required=False,
                                     widget=forms.Select(attrs=lista))
        if nombre in ("llamado_desde", "llamado_hasta"):  # opcionales: vacíos = desde siempre / hasta hoy
            return forms.DateField(required=False, widget=fecha)
        if nombre == "estado":
            return forms.ChoiceField(choices=[("", "Todos")] + list(self.reporte.estados), required=False,
                                     widget=forms.Select(attrs=lista))
        raise ValueError(f"Filtro desconocido: {nombre}")

    def clean_mes(self):
        texto = self.cleaned_data.get("mes") or _mes_actual()
        try:
            anio, mes = (int(x) for x in texto.split("-"))
            date(anio, mes, 1)
        except (ValueError, OverflowError):
            raise forms.ValidationError("Elija un mes válido.") from None
        return anio, mes

    def clean(self):
        datos = super().clean()
        hoy = timezone.localdate()
        if "desde" in self.fields:
            desde, hasta = datos.get("desde") or hoy.replace(day=1), datos.get("hasta") or hoy
            datos["desde"], datos["hasta"] = (desde, hasta) if desde <= hasta else (hasta, desde)
        desde, hasta = datos.get("llamado_desde"), datos.get("llamado_hasta")
        if desde and hasta and desde > hasta:
            datos["llamado_desde"], datos["llamado_hasta"] = hasta, desde
        if "vendedor" in self.reporte.filtros and self.usuario.rol == Rol.VENDEDOR:
            datos["vendedor"] = getattr(self.usuario, "empleado", None)
        return datos

    def valores(self) -> dict:
        """Filtros listos para el reporte, con los valores por defecto aplicados."""
        return {k: v for k, v in self.cleaned_data.items() if v not in (None, "")}

    def texto(self) -> str:
        """'Equipo: Visado · Vendedor: Mora Lucía' (para el PDF; las fechas ya van en el periodo)."""
        partes = []
        for nombre in self.reporte.filtros:
            if nombre in ("desde", "hasta", "mes", "llamado_desde", "llamado_hasta"):  # van en el periodo
                continue
            valor = self.cleaned_data.get(nombre)
            if valor:
                if nombre == "estado":
                    valor = dict(self.reporte.estados).get(valor, valor)
                elif isinstance(valor, date):
                    valor = f"{valor:%d/%m/%Y}"
                elif nombre == "tipo_equipo":
                    from apps.ti.models import TipoEquipo

                    valor = dict(TipoEquipo.choices).get(valor, valor)
                partes.append(f"{ETIQUETAS[nombre]}: {valor}")
        return " · ".join(partes)

    def serializado(self) -> dict:
        """Para el registro de reportes generados."""
        datos = {}
        for nombre, valor in self.cleaned_data.items():
            if valor in (None, ""):
                continue
            if hasattr(valor, "pk"):
                datos[nombre] = {"id": valor.pk, "texto": str(valor)}
            elif isinstance(valor, tuple):
                datos[nombre] = f"{valor[0]:04d}-{valor[1]:02d}"
            else:
                datos[nombre] = str(valor)
        return datos
