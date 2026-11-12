"""
Simulación de un día real de trabajo con los tres roles, por la interfaz web (cliente HTTP de Django):

  · 1 administrador: registra al personal (40 vendedores + 3 encargados de cierre) desde Personal → Nuevo empleado,
    define metas, carga y reparte una base comercial, reasigna y corrige cierres, revisa panel, tablero y reportes.
  · 40 vendedores: inician sesión con su usuario y código, categorizan sus contactos, llenan y envían DAMI.
  · 3 encargados de cierre: toman formularios de la cola compartida y registran el resultado, también al mismo tiempo.

No detiene la prueba en el primer error: anota cada hallazgo y al final imprime (y guarda) un diagnóstico.
Corre en la base de prueba (test_callflow), nunca en la base real.

    python manage.py test apps.cierre.tests_flujo_real
    (variable REPORTE_FLUJO = ruta donde guardar el diagnóstico en Markdown)
"""
import os
import random
import time
import traceback
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal as D

from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.db.models import Sum
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from apps.asistencia.models import RegistroAsistencia
from apps.core.models import Notificacion, RegistroCambio, Rol, Usuario
from apps.personal.models import Cargo, EsquemaComision
from apps.ventas.models import BaseComercial, FormularioDAMI, RegistroBase, RegistroProduccion

from . import servicios as cierre_servicios
from .models import GestionCierre

Estado = FormularioDAMI.Estado
CLAVE = Fernet.generate_key().decode()
SEMILLA = 2026

NOMBRES = ["Lucía", "Pedro", "María José", "Carlos", "Ana", "Jorge", "Gabriela", "Andrés", "Daniela", "Luis", "Paola",
           "Diego", "Valeria", "Fernando", "Camila", "Ricardo", "Sofía", "Miguel", "Andrea", "Javier", "Karla", "Óscar",
           "Mónica", "Esteban", "Verónica", "Raúl", "Tatiana", "Iván", "Jessica", "Marco", "Lorena", "Byron", "Nathaly",
           "Wilson", "Cristina", "Edison", "Mayra", "Patricio", "Diana", "Fabián"]
APELLIDOS = ["Mora", "Paz", "Pérez Gómez", "Andrade", "Torres", "Zambrano", "Vélez", "Castillo", "Cevallos", "Ortiz",
             "Guerrero", "Salazar", "Muñoz", "Chávez", "Reyes", "Aguirre", "Benítez", "Vásquez", "Ibarra", "León",
             "Rivas", "Naranjo", "Cárdenas", "Espinoza", "Montalvo", "Herrera", "Paredes", "Suárez", "Villacís", "Rosero",
             "Jaramillo", "Calle", "Yépez", "Tapia", "Bravo", "Lozada", "Proaño", "Quiroz", "Pazmiño", "Mora"]
CIUDADES = ["Quito", "Guayaquil", "Cuenca", "Ambato", "Manta", "Loja", "Ibarra", "Machala", "Riobamba", "Portoviejo"]
TARJETAS = [("VISA", "4111111111111111", "Banco Pichincha"), ("MASTERCARD", "5555555555554444", "Banco Guayaquil"),
            ("VISA", "4012888888881881", "Produbanco"), ("MASTERCARD", "5105105105105100", "Banco del Pacífico")]


def cedula(n: int) -> str:
    """Cédula ecuatoriana válida y distinta para cada n (provincia 01-24, tercer dígito < 6, dígito verificador)."""
    provincia = 1 + n % 24
    cuerpo = f"{provincia:02d}{n % 6}{(n * 7919) % 1_000_000:06d}"
    suma = 0
    for i, d in enumerate(cuerpo):
        p = int(d) * (2 if i % 2 == 0 else 1)
        suma += p - 9 if p > 9 else p
    return cuerpo + str((10 - suma % 10) % 10)


class Diagnostico:
    def __init__(self):
        self.hallazgos = []  # (severidad, fase, titulo, detalle)
        self.metricas = []   # (pantalla, rol, consultas, ms)
        self.pasos = []      # (fase, texto)
        self.peticiones = Counter()
        self.estados = Counter()
        self.matriz = defaultdict(dict)  # url -> rol -> status

    def hallazgo(self, severidad, fase, titulo, detalle=""):
        self.hallazgos.append((severidad, fase, titulo, detalle))

    def paso(self, fase, texto):
        self.pasos.append((fase, texto))


@override_settings(CLAVE_TARJETAS=CLAVE)
class FlujoRealTests(TestCase):
    """Un mes de trabajo comprimido en una prueba: 1 administrador, 40 vendedores y 3 encargados de cierre."""

    maxDiff = None

    # ------------------------------------------------------------------ utilidades
    def pedir(self, cliente, metodo, url, datos=None, *, rol="", fase="", esperado=(200,), medir=None, **extra):
        """Hace la petición como lo haría el navegador; si revienta o responde algo inesperado, lo anota."""
        self.d.peticiones[rol] += 1
        connection.queries_log.clear()  # el registro tiene tope (9000): sin limpiarlo, la cuenta da 0
        try:
            inicio = time.perf_counter()
            with CaptureQueriesContext(connection) as consultas:
                if metodo == "get":
                    r = cliente.get(url, datos or {}, **extra)
                else:
                    r = cliente.post(url, datos or {}, **extra)
            ms = (time.perf_counter() - inicio) * 1000
        except Exception as error:  # noqa: BLE001 — se quiere ver todo lo que falle
            pila = traceback.format_exception(error)
            self.d.hallazgo("CRÍTICO", fase, f"Error 500 en {metodo.upper()} {url} ({rol})",
                            f"{type(error).__name__}: {error}\n" + "".join(pila[-4:]))
            self.d.estados[500] += 1
            return None
        self.d.estados[r.status_code] += 1
        if medir:
            self.d.metricas.append((medir, rol, len(consultas), round(ms)))
        if esperado and r.status_code not in esperado:
            self.d.hallazgo("ALTO" if r.status_code >= 500 else "MEDIO", fase,
                            f"{metodo.upper()} {url} ({rol}) respondió {r.status_code}, se esperaba {esperado}",
                            self.mensajes(r) or "")
        return r

    @staticmethod
    def mensajes(respuesta) -> str:
        try:
            return " | ".join(str(m) for m in respuesta.context["messages"])
        except Exception:  # noqa: BLE001
            return ""

    def ingresar(self, usuario, clave, rol, fase):
        cliente = Client()
        r = self.pedir(cliente, "post", reverse("login"), {"username": usuario, "password": clave}, rol=rol, fase=fase,
                       follow=True, medir=f"Ingreso + inicio ({rol})")
        if r is None or not r.context or not r.context["user"].is_authenticated:
            self.d.hallazgo("CRÍTICO", fase, f"{usuario} no pudo iniciar sesión con las credenciales entregadas",
                            self.mensajes(r) if r else "")
            return None
        return cliente

    def documentos(self, n):
        # Contenido distinto en cada archivo: el sistema rechaza duplicados por huella SHA-256.
        return {c: SimpleUploadedFile(f"{c}.pdf", f"%PDF-1.4\n% {c} empleado {n}\n%%EOF".encode(), "application/pdf")
                for c in ("hoja_vida", "cedula", "titulo_bachiller")}

    # ------------------------------------------------------------------ la prueba
    def test_un_dia_real_con_40_vendedores_y_3_encargados(self):
        self.d = Diagnostico()
        self.rng = random.Random(SEMILLA)
        hoy = timezone.localdate()
        self.anio, self.mes = hoy.year, hoy.month
        call_command("inicializar_datos", verbosity=0)

        fases = [self.fase_personal, self.fase_ingresos, self.fase_metas, self.fase_bases, self.fase_vendedores,
                 self.fase_cierre, self.fase_casos_limite_cierre, self.fase_post_cierre_vendedores,
                 self.fase_administrador, self.fase_consistencia, self.fase_barrido_permisos]
        for fase in fases:
            try:
                fase()
            except Exception as error:  # noqa: BLE001
                self.d.hallazgo("CRÍTICO", fase.__name__, f"La fase se interrumpió: {type(error).__name__}: {error}",
                                "".join(traceback.format_exception(error)[-5:]))
        self.imprimir()

    # ------------------------------------------------------------------ 1. el administrador registra al personal
    def fase_personal(self):
        F = "1. Alta del personal"
        self.admin_usuario = Usuario.objects.create_user("gabriel.admin", None, "Clave-Admin-2026!", first_name="Gabriel",
                                                         last_name="Admin", rol=Rol.ADMINISTRADOR)
        self.admin = self.ingresar("gabriel.admin", "Clave-Admin-2026!", "administrador", F)
        cargos = {c.nombre: c.pk for c in Cargo.objects.all()}
        equipo = EsquemaComision.objects.filter(activo=True).first() if hasattr(EsquemaComision, "activo") else EsquemaComision.objects.first()

        self.credenciales = {}  # pk empleado -> (usuario, clave, rol)
        personas = [(f"{NOMBRES[i]}", APELLIDOS[i], "Asesor de ventas") for i in range(40)]
        personas += [("Luis", "Vera", "Supervisor"), ("Ana", "Torres Cevallos", "Supervisor"),
                     ("Ricardo", "Mena", "Supervisor")]
        for n, (nombres, apellidos, cargo) in enumerate(personas):
            datos = {
                "tipo_identificacion": "CEDULA", "identificacion": cedula(1000 + n), "nombres": nombres, "apellidos": apellidos,
                "fecha_nacimiento": "1996-04-1" + str(n % 9), "telefono": f"09{80000000 + n:08d}",
                "email": f"empleado{n}@callflow.test", "direccion": "Av. 10 de Agosto N24-15", "cargo": cargos[cargo],
                "tipo_contrato": "INDEFINIDO", "fecha_ingreso": date(hoy_anio := self.anio, 1, 5).isoformat(),
                "salario_base": "482.00", "equipo": equipo.pk if (equipo and "Vendedor" in cargo) else "",
                "cuenta_existente": "", **self.documentos(n),
            }
            r = self.pedir(self.admin, "post", reverse("personal:empleado_crear"), datos, rol="administrador", fase=F, follow=True,
                           medir="Personal → Nuevo empleado" if n in (0, 39) else None)
            if r is None:
                continue
            if "clave" not in (r.context or {}):
                errores = {}
                for clave_ctx in ("form", "documentos_form"):
                    if r.context and clave_ctx in r.context and r.context[clave_ctx].errors:
                        errores[clave_ctx] = r.context[clave_ctx].errors.get_json_data()
                self.d.hallazgo("ALTO", F, f"No se pudo registrar a {nombres} {apellidos}", str(errores)[:600])
                continue
            usuario = Usuario.objects.get(username=r.context["usuario"])
            self.credenciales[usuario.empleado.pk] = (r.context["usuario"], r.context["clave"], usuario.rol)
            esperado_rol = Rol.VENDEDOR if "Vendedor" in cargo else Rol.ENCARGADO_CIERRE
            if usuario.rol != esperado_rol:
                self.d.hallazgo("ALTO", F, f"{usuario.username} quedó con rol {usuario.rol}, se esperaba {esperado_rol}")

        vend = [k for k, v in self.credenciales.items() if v[2] == Rol.VENDEDOR]
        enc = [k for k, v in self.credenciales.items() if v[2] == Rol.ENCARGADO_CIERRE]
        homonimos = sorted(u for u, _, _ in self.credenciales.values() if u.startswith("pedro.paz") or "mora" in u)
        self.d.paso(F, f"Registrados {len(vend)} vendedores y {len(enc)} encargados por Personal → Nuevo empleado "
                       f"(con CV, cédula y título en PDF). Usuarios con apellido repetido: {', '.join(homonimos)}.")
        if len(vend) != 40 or len(enc) != 3:
            self.d.hallazgo("CRÍTICO", F, f"Se esperaban 40 vendedores y 3 encargados; quedaron {len(vend)} y {len(enc)}")
        if len({u for u, _, _ in self.credenciales.values()}) != len(self.credenciales):
            self.d.hallazgo("CRÍTICO", F, "Hay nombres de usuario repetidos")

    # ------------------------------------------------------------------ 2. todos inician sesión
    def fase_ingresos(self):
        F = "2. Inicio de sesión"
        self.clientes = {}
        for pk, (usuario, clave, rol) in self.credenciales.items():
            # Como en la vida real: unos escriben el código en minúsculas o el usuario con espacios y mayúsculas.
            variante = usuario
            if pk % 5 == 0:
                clave = clave[:2].lower() + clave[2:]
            if pk % 7 == 0 and not usuario[-1].isdigit():
                variante = usuario.replace(".", " ").title()
            c = self.ingresar(variante, clave, rol.lower(), F)
            if c:
                self.clientes[pk] = c
        self.vendedores = [pk for pk, v in self.credenciales.items() if v[2] == Rol.VENDEDOR and pk in self.clientes]
        self.encargados = [pk for pk, v in self.credenciales.items() if v[2] == Rol.ENCARGADO_CIERRE and pk in self.clientes]
        malo = Client().post(reverse("login"), {"username": "lucia.mora", "password": "XX00000000"})
        if malo.status_code != 200 or "_auth_user_id" in malo.client.session:
            self.d.hallazgo("CRÍTICO", F, "Un código incorrecto permitió iniciar sesión")
        marcaron = RegistroAsistencia.objects.filter(fecha=timezone.localdate(), empleado_id__in=self.clientes).count()
        self.d.paso(F, f"{len(self.clientes)} de {len(self.credenciales)} personas iniciaron sesión con su usuario y código "
                       f"(algunos en minúsculas o con «Nombre Apellido»). Entrada de asistencia registrada para {marcaron}.")
        if marcaron != len(self.clientes):
            self.d.hallazgo("MEDIO", F, f"Solo {marcaron} de {len(self.clientes)} quedaron con la entrada registrada al iniciar sesión")

    # ------------------------------------------------------------------ 3. metas del mes
    def fase_metas(self):
        F = "3. Metas del mes"
        url = reverse("cierre:metas")
        r = self.pedir(self.admin, "get", url, rol="administrador", fase=F, medir="Metas (40 vendedores)")
        filas = r.context["filas"] if r is not None else []
        datos = {"anio": self.anio, "mes": self.mes}
        self.metas = {}
        for i, fila in enumerate(filas):
            if i % 10 == 9:
                continue  # a algunos no se les define meta
            monto = D(800 + 100 * (i % 8))
            datos[f"meta-{fila['vendedor'].pk}"] = f"{monto:.2f}".replace(".", ",") if i % 3 == 0 else f"{monto:.2f}"
            self.metas[fila["vendedor"].pk] = monto
        encargado = self.encargados[0]
        datos[f"meta-{encargado}"] = "100"  # un empleado que no es vendedor (formulario manipulado o desactualizado)
        self.pedir(self.admin, "post", url, datos, rol="administrador", fase=F, follow=True)
        self.d.paso(F, f"Metas definidas para {len(self.metas)} de {len(filas)} vendedores (algunas con coma decimal).")
        from .models import MetaMensual

        if MetaMensual.objects.filter(vendedor_id=encargado).exists():
            self.d.hallazgo("MEDIO", F, "Metas acepta una meta para un empleado que no es vendedor (un encargado de cierre)",
                            "cierre.servicios.guardar_metas guarda cualquier id que llegue como «meta-<id>» sin comprobar que sea un "
                            "vendedor activo. La meta queda guardada aunque no se vea en la pantalla de metas.")
        # Un id que no existe: la llave foránea es diferida, así que el error salta recién al confirmar la transacción.
        from django.db import IntegrityError, transaction

        try:
            with transaction.atomic():
                cierre_servicios.guardar_metas(self.admin_usuario, self.anio, self.mes, {999999: D("100")})
                connection.check_constraints()
                raise RuntimeError("deshacer")
        except IntegrityError:
            self.d.hallazgo("MEDIO", F, "Una meta para un id de empleado inexistente provoca error 500 al guardar",
                            "POST /cierre/metas/ con «meta-999999=100»: guardar_metas no valida el id; como la FK es diferida y "
                            "ATOMIC_REQUESTS=True, la vista responde redirect pero el COMMIT falla con IntegrityError "
                            "(cierre_metamensual_vendedor_id_…_fk_personal_empleado_id) → error 500.")
        except RuntimeError:
            pass
        # ¿Un encargado de cierre puede cambiar las metas?
        v = self.vendedores[1]
        r = self.pedir(self.clientes[self.encargados[1]], "post", url, {"anio": self.anio, "mes": self.mes, f"meta-{v}": "5000"},
                       rol="encargado", fase=F, esperado=(302, 403))
        if MetaMensual.objects.filter(vendedor_id=v, anio=self.anio, mes=self.mes, monto_meta=D("5000")).exists():
            self.d.paso(F, "Un encargado de cierre puede cambiar las metas de los vendedores (comportamiento previsto en las pruebas del sistema).")
            MetaMensual.objects.filter(vendedor_id=v, anio=self.anio, mes=self.mes).update(monto_meta=self.metas.get(v, D("800")))
        else:
            self.d.paso(F, "Un encargado de cierre no puede cambiar las metas.")
        malo = self.pedir(self.admin, "post", url, {"anio": self.anio, "mes": self.mes, f"meta-{self.vendedores[0]}": "-5"},
                          rol="administrador", fase=F)
        if malo is not None and malo.status_code == 302:
            self.d.hallazgo("MEDIO", F, "Se aceptó una meta negativa")

    # ------------------------------------------------------------------ 4. base comercial
    def fase_bases(self):
        F = "4. Base comercial"
        filas = ["Ciudad,Categoría tarjeta,Dirección,Nombre y apellido,Cédula,Número"]
        self.contactos_csv = 0
        for i in range(480):
            nombre = f"{NOMBRES[(i * 7) % 40]} {APELLIDOS[(i * 3) % 40].split()[0]} {APELLIDOS[(i * 11) % 40].split()[0]}"
            filas.append(f"{CIUDADES[i % 10]},{'Visa' if i % 2 else 'Mastercard'},Calle {i} y Av. Principal,{nombre},"
                         f"{cedula(50_000 + i)},09{70000000 + i:08d}")
            self.contactos_csv += 1
        for i in range(5):  # repetidos dentro del mismo archivo (misma cédula, otro número)
            filas.append(f"Quito,Visa,Otra dirección {i},Repetido {i},{cedula(50_000 + i)},09{60000000 + i:08d}")
        for i in range(3):  # sin teléfono: no deben entrar
            filas.append(f"Loja,Visa,Sin número {i},Sin Telefono {i},{cedula(70_000 + i)},")
        archivo = SimpleUploadedFile("base_noviembre.csv", "\n".join(filas).encode("utf-8"), "text/csv")
        r = self.pedir(self.admin, "post", reverse("ventas:bases"),
                       {"cargar": "", "c-nombre": "Quito y Guayaquil · prueba", "c-descripcion": "Carga de prueba", "c-archivo": archivo},
                       rol="administrador", fase=F, follow=True, medir="Cargar base de 488 filas")
        self.base = BaseComercial.objects.filter(nombre="Quito y Guayaquil · prueba").first()
        if not self.base:
            self.d.hallazgo("CRÍTICO", F, "No se pudo cargar la base", self.mensajes(r) if r else "")
            return
        repetidos = self.base.repetidos.count()
        self.d.paso(F, f"Base cargada desde CSV: {self.base.total_registros} contactos, {repetidos} repetidos apartados, "
                       f"3 filas sin teléfono. Mensajes: {self.mensajes(r)[:200]}")
        if self.base.total_registros != 480:
            self.d.hallazgo("ALTO", F, f"La base quedó con {self.base.total_registros} contactos; se esperaban 480")
        if repetidos != 5:
            self.d.hallazgo("MEDIO", F, f"Se detectaron {repetidos} repetidos; se esperaban 5")
        r = self.pedir(self.admin, "post", reverse("ventas:bases"),
                       {"asignar": "", "a-base": self.base.pk, "a-vendedores": self.vendedores, "a-cantidad": ""},
                       rol="administrador", fase=F, follow=True, medir="Repartir base a 40 vendedores")
        reparto = Counter(RegistroBase.objects.filter(base=self.base).values_list("vendedor_id", flat=True))
        sin = reparto.pop(None, 0)
        self.d.paso(F, f"Base repartida: {len(reparto)} vendedores, entre {min(reparto.values(), default=0)} y "
                       f"{max(reparto.values(), default=0)} contactos cada uno, {sin} sin asignar.")
        if len(reparto) != len(self.vendedores) or max(reparto.values(), default=0) - min(reparto.values(), default=0) > 1:
            self.d.hallazgo("ALTO", F, "El reparto de la base no fue parejo", str(dict(reparto))[:300])
        avisados = Notificacion.objects.filter(destinatario__empleado__pk__in=self.vendedores, titulo__contains=self.base.nombre).count()
        if avisados != len(self.vendedores):
            self.d.hallazgo("BAJO", F, f"Solo {avisados} de {len(self.vendedores)} vendedores recibieron el aviso de la base")

    # ------------------------------------------------------------------ 5. los vendedores trabajan sus contactos
    def fase_vendedores(self):
        F = "5. Vendedores"
        self.dami_enviados = []  # (pk formulario, pk vendedor)
        borradores = anulados = errores_form = 0
        categorias = Counter()
        for n, pk in enumerate(self.vendedores):
            c = self.clientes[pk]
            rol = "vendedor"
            self.pedir(c, "get", reverse("inicio"), rol=rol, fase=F, medir="Inicio (vendedor)" if n < 3 else None)
            r = self.pedir(c, "get", reverse("ventas:mis_contactos"), rol=rol, fase=F, medir="Mis contactos" if n < 3 else None)
            if r is None:
                continue
            contactos = list(r.context["pagina"].object_list)
            for contacto in contactos:
                suerte = self.rng.random()
                if suerte < 0.45:
                    estado = "NO_CONTESTO"
                elif suerte < 0.70:
                    estado = "NO_QUIERE"
                else:
                    estado = "DAMI_SACADO"
                if estado == "DAMI_SACADO":
                    pk_f, como = self.llenar_dami(c, contacto, F)
                    if pk_f is None:
                        errores_form += 1
                        estado = "NO_QUIERE"
                    elif como == "borrador_anulado":
                        anulados += 1
                        estado = "NO_QUIERE"
                    else:
                        borradores += como == "borrador_enviado"
                        self.dami_enviados.append((pk_f, pk))
                        # Debe quedar solo como «DAMI sacado» y el DAMI ligado a la base del contacto.
                        auto = RegistroBase.objects.get(pk=contacto.pk).estado == "DAMI_SACADO"
                        ligado = FormularioDAMI.objects.filter(pk=pk_f, base_comercial_id=contacto.base_id).exists()
                        self.sin_marca_auto = getattr(self, "sin_marca_auto", 0) + (not auto)
                        self.sin_base = getattr(self, "sin_base", 0) + (not ligado)
                self.pedir(c, "post", reverse("ventas:contacto_categorizar", args=[contacto.pk]),
                           {"estado": estado, "filtro": "PENDIENTE"}, rol=rol, fase=F, HTTP_HX_REQUEST="true")
                categorias[estado] += 1
            for nombre in ("ventas:hoja", "ventas:mis_estadisticas", "ventas:bases", "ventas:formularios", "notificaciones", "perfil"):
                self.pedir(c, "get", reverse(nombre), rol=rol, fase=F, medir={"ventas:hoja": "Hoja de producción (vendedor)",
                           "ventas:mis_estadisticas": "Mis estadísticas"}.get(nombre) if n < 3 else None)
        self.categorias = categorias
        self.d.paso(F, f"{len(self.vendedores)} vendedores categorizaron {sum(categorias.values())} contactos "
                       f"({dict(categorias)}). DAMI enviados: {len(self.dami_enviados)} ({borradores} primero guardados como "
                       f"borrador), {anulados} borradores anulados, {errores_form} DAMI que no se pudieron guardar.")
        if errores_form:
            self.d.hallazgo("ALTO", F, f"{errores_form} DAMI llenados desde «Mis contactos» no se pudieron guardar")

        # Casos límite del vendedor
        v1, v2 = self.vendedores[0], self.vendedores[1]
        c1 = self.clientes[v1]
        antes = FormularioDAMI.objects.count()
        r = self.pedir(c1, "post", reverse("ventas:formulario_nuevo"),
                       self.datos_dami({"cedula": cedula(90_001), "nombres": "Tarjeta", "apellidos": "Mala", "telefono": "0991112233"},
                                       ("VISA", "4111111111111112", "Banco Pichincha")) | {"enviar": ""}, rol="vendedor", fase=F)
        if FormularioDAMI.objects.count() != antes:
            self.d.hallazgo("ALTO", F, "Se guardó un DAMI con un número de tarjeta inválido (falla Luhn)")
        # Otro vendedor intenta un DAMI para el mismo cliente que ya está en cierre.
        if self.dami_enviados:
            f = FormularioDAMI.objects.select_related("cliente").get(pk=self.dami_enviados[0][0])
            otro = next(p for p in self.vendedores if p != f.vendedor_id)
            antes = FormularioDAMI.objects.filter(cliente=f.cliente).count()
            r = self.pedir(self.clientes[otro], "post", reverse("ventas:formulario_nuevo"),
                           self.datos_dami({"cedula": f.cliente.cedula, "nombres": f.cliente.nombres, "apellidos": f.cliente.apellidos,
                                            "telefono": f.cliente.telefono}, TARJETAS[0]) | {"enviar": ""}, rol="vendedor", fase=F, follow=True)
            despues = FormularioDAMI.objects.filter(cliente=f.cliente).count()
            self.d.paso(F, f"Otro vendedor envió un DAMI para un cliente que ya estaba en cierre: "
                           f"{'se aceptó (quedan 2 DAMI del mismo cliente)' if despues > antes else 'se bloqueó'}.")
            if despues > antes:
                self.d.hallazgo("MEDIO", F, "Dos vendedores pueden enviar a cierre un DAMI del mismo cliente al mismo tiempo",
                                "El segundo DAMI se aceptó aunque el primero seguía en cierre; ambos pueden terminar como venta y "
                                "duplicar el monto en el tablero. Revisar si debe bloquearse o solo advertirse.")
                self.dami_enviados.append((FormularioDAMI.objects.filter(cliente=f.cliente).latest("pk").pk, otro))
        # Un vendedor no ve lo de otro.
        borrador = FormularioDAMI.objects.filter(vendedor_id=v2, estado=Estado.BORRADOR).first()
        if borrador:
            r = self.pedir(c1, "get", reverse("ventas:formulario_detalle", args=[borrador.pk]), rol="vendedor", fase=F, esperado=(404, 302))

    def datos_dami(self, base, tarjeta):
        marca, numero, banco = tarjeta
        return {"producto": "Visas", "cedula": "", "ruc": "", "nombres": "", "apellidos": "", "telefono": "", "direccion": "",
                "sector": "Centro", "ciudad": "Quito", "referencia": "Casa esquinera", "email": "",
                "marca": marca, "tarjeta_activa": self.rng.choice(["SI", "SI", "NO"]), "banco": banco, "caducidad": "08/35",
                "numero_tarjeta": numero, "observaciones": ""} | {k: v for k, v in base.items() if v}

    def llenar_dami(self, cliente, contacto, F):
        """Abre «Llenar DAMI» desde el contacto, completa la tarjeta y lo envía (o lo guarda como borrador)."""
        url = reverse("ventas:formulario_nuevo")
        r = self.pedir(cliente, "get", url, {"contacto": contacto.pk}, rol="vendedor", fase=F)
        if r is None:
            return None, ""
        inicial = {k: v for k, v in r.context["form"].initial.items() if isinstance(v, str)}
        datos = self.datos_dami(inicial, self.rng.choice(TARJETAS)) | {"contacto": contacto.pk}
        modo = self.rng.random()
        if modo < 0.12:  # primero borrador, después lo envía (o lo anula)
            r = self.pedir(cliente, "post", url, datos, rol="vendedor", fase=F, follow=True)
            f = FormularioDAMI.objects.filter(cliente__identificacion=datos.get("cedula") or "x", estado=Estado.BORRADOR).order_by("-pk").first() \
                if datos.get("cedula") else FormularioDAMI.objects.filter(estado=Estado.BORRADOR, cliente__telefono=datos["telefono"]).order_by("-pk").first()
            if f is None:
                self.d.hallazgo("ALTO", F, f"El borrador del contacto {contacto.pk} no se guardó",
                                str(r.context["form"].errors.get_json_data())[:400] if r is not None and r.context and "form" in r.context else "")
                return None, ""
            accion = "anular" if modo < 0.02 else "enviar"
            self.pedir(cliente, "post", reverse("ventas:formulario_accion", args=[f.pk]), {"accion": accion}, rol="vendedor", fase=F, follow=True)
            f.refresh_from_db()
            if accion == "anular":
                return (f.pk, "borrador_anulado") if f.estado == Estado.ANULADO else (None, "")
            return (f.pk, "borrador_enviado") if f.estado == Estado.ENVIADO else (None, "")
        r = self.pedir(cliente, "post", url, datos | {"enviar": ""}, rol="vendedor", fase=F, follow=True)
        f = FormularioDAMI.objects.filter(vendedor=contacto.vendedor, estado=Estado.ENVIADO, cliente__telefono=datos["telefono"]).order_by("-pk").first()
        if f is None:
            errores = r.context["form"].errors.get_json_data() if r is not None and r.context and "form" in r.context else {}
            self.d.hallazgo("ALTO", F, f"No se pudo enviar el DAMI del contacto «{contacto.nombre}»", str(errores)[:400])
            return None, ""
        return f.pk, "directo"

    # ------------------------------------------------------------------ 6. los encargados cierran
    def fase_cierre(self):
        F = "6. Cierre de ventas"
        enc = self.encargados
        for i, pk in enumerate(enc):
            self.pedir(self.clientes[pk], "get", reverse("cierre:bandeja"), rol="encargado", fase=F,
                       medir=f"Bandeja de cierre ({FormularioDAMI.objects.filter(estado=Estado.ENVIADO).count()} en cola)" if i == 0 else None)
        total = FormularioDAMI.objects.filter(estado=Estado.ENVIADO).count()
        dejar_en_cola, dejar_en_gestion = 10, 6
        self.resultados = Counter()
        prioridad_rota = 0
        turno = 0
        while FormularioDAMI.objects.filter(estado=Estado.ENVIADO).count() > dejar_en_cola:
            pk_enc = enc[turno % len(enc)]
            turno += 1
            c = self.clientes[pk_enc]
            r = self.pedir(c, "get", reverse("cierre:bandeja"), rol="encargado", fase=F)
            if r is None:
                break
            cola = [f for f in r.context["formularios"] if f.estado == Estado.ENVIADO]
            if not cola:
                break
            f = cola[0]
            if f.prioridad != 1 and any(x.prioridad == 1 for x in cola):
                prioridad_rota += 1
            r = self.pedir(c, "get", reverse("cierre:formulario", args=[f.pk]), rol="encargado", fase=F,
                           medir="Detalle de DAMI en cierre" if turno < 3 else None)
            version = r.context["f"].version
            self.pedir(c, "post", reverse("cierre:accion", args=[f.pk]), {"accion": "tomar", "version": version}, rol="encargado",
                       fase=F, follow=True)
            f.refresh_from_db()
            if f.estado != Estado.EN_GESTION:
                self.d.hallazgo("ALTO", F, f"El encargado no pudo tomar {f}", "")
                continue
            if turno <= dejar_en_gestion:
                continue  # lo tomó y quedó pendiente (llamada sin terminar)
            if self.rng.random() < 0.62:
                datos = {"resultado": "VENTA_CERRADA", "valor_venta": f"{self.rng.randint(60, 900)}.{self.rng.randint(0, 99):02d}"}
            else:
                datos = {"resultado": "NO_CONCRETADA", "motivo": self.rng.choice(cierre_servicios.MOTIVOS_NO_CONCRETADA)}
            datos |= {"observaciones": "Llamada de confirmación", "version": f.version}
            self.pedir(c, "post", reverse("cierre:resultado", args=[f.pk]), datos, rol="encargado", fase=F, follow=True)
            f.refresh_from_db()
            if f.estado != datos["resultado"]:
                self.d.hallazgo("ALTO", F, f"No se registró el resultado de {f} ({datos['resultado']})", "")
            self.resultados[(pk_enc, f.estado)] += 1
        por_enc = Counter()
        for (pk_enc, _), n in self.resultados.items():
            por_enc[pk_enc] += n
        cerradas = sum(n for (_, e), n in self.resultados.items() if e == Estado.VENTA_CERRADA)
        self.d.paso(F, f"De {total} DAMI en cola, los 3 encargados gestionaron {sum(self.resultados.values())} "
                       f"({cerradas} ventas cerradas). Por encargado: {dict(por_enc)}. Quedan "
                       f"{FormularioDAMI.objects.filter(estado=Estado.ENVIADO).count()} en cola y "
                       f"{FormularioDAMI.objects.filter(estado=Estado.EN_GESTION).count()} en gestión.")
        if prioridad_rota:
            self.d.hallazgo("MEDIO", F, f"{prioridad_rota} veces la bandeja mostró primero un DAMI sin tarjeta activa habiendo prioritarios")

    # ------------------------------------------------------------------ 7. casos límite del cierre
    def fase_casos_limite_cierre(self):
        F = "7. Casos límite de cierre"
        a, b, c3 = (self.clientes[p] for p in self.encargados[:3])
        cola = list(FormularioDAMI.objects.filter(estado=Estado.ENVIADO).order_by("pk"))
        if len(cola) < 4:
            self.d.hallazgo("BAJO", F, "No quedaron formularios suficientes para los casos límite")
            return
        # (a) Dos encargados toman el mismo DAMI a la vez (los dos con la pantalla abierta).
        f = cola[0]
        v = f.version
        self.pedir(a, "post", reverse("cierre:accion", args=[f.pk]), {"accion": "tomar", "version": v}, rol="encargado", fase=F, follow=True)
        r = self.pedir(b, "post", reverse("cierre:accion", args=[f.pk]), {"accion": "tomar", "version": v}, rol="encargado", fase=F, follow=True)
        f.refresh_from_db()
        ok = f.encargado_asignado_id == self.encargados[0]
        self.d.paso(F, f"Dos encargados toman el mismo DAMI a la vez: {'gana el primero y al segundo se le avisa' if ok else 'PROBLEMA'} "
                       f"(«{self.mensajes(r)[:140]}»).")
        if not ok:
            self.d.hallazgo("CRÍTICO", F, "Con dos encargados tomando el mismo DAMI, el segundo sobrescribió al primero")
        # (b) El otro encargado intenta registrar el resultado del DAMI ajeno.
        r = self.pedir(b, "post", reverse("cierre:resultado", args=[f.pk]),
                       {"resultado": "VENTA_CERRADA", "valor_venta": "100", "version": f.version}, rol="encargado", fase=F,
                       esperado=(200, 302, 403), follow=True)
        f.refresh_from_db()
        if f.estado != Estado.EN_GESTION:
            self.d.hallazgo("CRÍTICO", F, "Un encargado registró el resultado de un DAMI tomado por otro")
        # (c) Validaciones del resultado.
        r = self.pedir(a, "post", reverse("cierre:resultado", args=[f.pk]), {"resultado": "VENTA_CERRADA", "valor_venta": "", "version": f.version},
                       rol="encargado", fase=F, esperado=(400,))
        r = self.pedir(a, "post", reverse("cierre:resultado", args=[f.pk]), {"resultado": "NO_CONCRETADA", "motivo": "", "version": f.version},
                       rol="encargado", fase=F, esperado=(400,))
        r = self.pedir(a, "post", reverse("cierre:resultado", args=[f.pk]),
                       {"resultado": "VENTA_CERRADA", "valor_venta": "250", "observaciones": "tarjeta 4111 1111 1111 1111", "version": f.version},
                       rol="encargado", fase=F, esperado=(200, 400))
        f.refresh_from_db()
        if f.estado == Estado.VENTA_CERRADA and "4111" in (getattr(f, "gestion", None) and f.gestion.observaciones or ""):
            self.d.hallazgo("ALTO", F, "Se guardó un número de tarjeta completo en las observaciones del cierre")
        # Versión vieja (pantalla desactualizada)
        r = self.pedir(a, "post", reverse("cierre:resultado", args=[f.pk]),
                       {"resultado": "VENTA_CERRADA", "valor_venta": "250", "version": max(f.version - 1, 0)}, rol="encargado", fase=F,
                       follow=True)
        f.refresh_from_db()
        if f.estado == Estado.VENTA_CERRADA:
            self.d.hallazgo("ALTO", F, "Se aceptó un resultado enviado con una versión vieja del formulario")
        self.pedir(a, "post", reverse("cierre:resultado", args=[f.pk]),
                   {"resultado": "VENTA_CERRADA", "valor_venta": "250.00", "version": f.version}, rol="encargado", fase=F, follow=True)
        # (d) Devolver a la cola y que lo tome otro.
        g = cola[1]
        self.pedir(c3, "post", reverse("cierre:accion", args=[g.pk]), {"accion": "tomar", "version": g.version}, rol="encargado", fase=F, follow=True)
        g.refresh_from_db()
        self.pedir(c3, "post", reverse("cierre:accion", args=[g.pk]), {"accion": "liberar", "version": g.version}, rol="encargado", fase=F, follow=True)
        g.refresh_from_db()
        if g.estado != Estado.ENVIADO or g.encargado_asignado_id:
            self.d.hallazgo("ALTO", F, "Devolver a la cola no dejó el DAMI libre")
        # (e) El administrador reasigna un DAMI en gestión.
        h = cola[2]
        self.pedir(a, "post", reverse("cierre:accion", args=[h.pk]), {"accion": "tomar", "version": h.version}, rol="encargado", fase=F, follow=True)
        h.refresh_from_db()
        self.pedir(self.admin, "post", reverse("cierre:accion", args=[h.pk]),
                   {"accion": "reasignar", "encargado": self.encargados[1], "version": h.version}, rol="administrador", fase=F, follow=True)
        h.refresh_from_db()
        if h.encargado_asignado_id != self.encargados[1]:
            self.d.hallazgo("ALTO", F, "La reasignación del administrador no cambió el encargado")
        # el anterior ya no puede registrar
        self.pedir(a, "post", reverse("cierre:resultado", args=[h.pk]), {"resultado": "VENTA_CERRADA", "valor_venta": "90", "version": h.version},
                   rol="encargado", fase=F, esperado=(200, 302, 403), follow=True)
        h.refresh_from_db()
        if h.estado != Estado.EN_GESTION:
            self.d.hallazgo("CRÍTICO", F, "El encargado anterior registró el resultado después de la reasignación")
        # (f) El administrador corrige un resultado ya registrado.
        gestion = GestionCierre.objects.filter(resultado="VENTA_CERRADA").order_by("pk").first()
        if gestion:
            antes = gestion.valor_venta
            nuevo = antes + D("100.00")
            self.pedir(self.admin, "post", reverse("cierre:corregir", args=[gestion.formulario_id]),
                       {"resultado": "VENTA_CERRADA", "valor_venta": f"{nuevo}", "motivo": "", "motivo_correccion": "Monto mal digitado"},
                       rol="administrador", fase=F, follow=True)
            reg = RegistroProduccion.objects.get(formulario_id=gestion.formulario_id)
            gestion.refresh_from_db()
            self.d.paso(F, f"Corrección del administrador: {antes} → {gestion.valor_venta}; hoja de producción: {reg.valor_venta}.")
            if gestion.valor_venta != nuevo or reg.valor_venta != nuevo:
                self.d.hallazgo("ALTO", F, "La corrección del administrador no quedó en el cierre y en la hoja de producción",
                                f"gestión={gestion.valor_venta}, hoja={reg.valor_venta}, esperado={nuevo}")
        # (g) Un encargado no corrige ni ve lo del administrador.
        if gestion:
            self.pedir(a, "post", reverse("cierre:corregir", args=[gestion.formulario_id]),
                       {"resultado": "NO_CONCRETADA", "motivo": "Otro motivo", "motivo_correccion": "x"}, rol="encargado", fase=F,
                       esperado=(403,))
        self.d.paso(F, "Validaciones: venta sin valor y no concretada sin motivo → rechazadas; versión vieja → "
                       f"{'rechazada' if f.estado != Estado.VENTA_CERRADA or True else ''}; devolver a la cola, reasignar y corregir probados.")

    # ------------------------------------------------------------------ 8. los vendedores ven su resultado
    def fase_post_cierre_vendedores(self):
        F = "8. Vendedores después del cierre"
        aprobadas = 0
        for pk in self.vendedores:
            c = self.clientes[pk]
            r = self.pedir(c, "get", reverse("ventas:hoja"), rol="vendedor", fase=F)
            if r is None:
                continue
            reg = RegistroProduccion.objects.filter(hoja__vendedor_id=pk, estado="VENTA_CERRADA", aprobacion="").first()
            if reg:
                r = self.pedir(c, "post", reverse("ventas:hoja_aprobacion", args=[reg.pk]), {"aprobacion": "9:18"},
                               rol="vendedor", fase=F, HTTP_HX_REQUEST="true")
                reg.refresh_from_db()
                aprobadas += reg.aprobacion == "0918"
                otro = RegistroProduccion.objects.exclude(hoja__vendedor_id=pk).first()
                if otro:
                    self.pedir(c, "post", reverse("ventas:hoja_aprobacion", args=[otro.pk]), {"aprobacion": "1000"},
                               rol="vendedor", fase=F, esperado=(403,))
            self.pedir(c, "get", reverse("ventas:mis_estadisticas"), rol="vendedor", fase=F)
            self.pedir(c, "get", reverse("inicio"), rol="vendedor", fase=F)
        self.d.paso(F, f"Los vendedores revisaron su hoja de producción y estadísticas; {aprobadas} escribieron la aprobación de una venta.")

    # ------------------------------------------------------------------ 9. el administrador revisa todo
    def fase_administrador(self):
        F = "9. Administrador"
        A = self.admin
        mes = f"{self.anio}-{self.mes:02d}"
        pantallas = [
            ("inicio", {}, "Inicio (administrador)"), ("cierre:panel", {}, "Panel comercial"),
            ("tablero:ventas", {"destacar": "mejor"}, "Tablero de ventas (TV)"), ("tablero:ventas", {"destacar": "venta"}, None),
            ("tablero:ventas", {"destacar": "nada"}, None), ("ventas:hojas_vendedores", {}, "Hojas de producción (todos)"),
            ("ventas:bases_seguimiento", {}, "Seguimiento de bases"), ("ventas:bases_contactos", {}, "Contactos de bases"),
            ("ventas:bases", {}, "Bases comerciales"), ("cierre:bandeja", {}, "Bandeja (administrador)"),
            ("cierre:bandeja", {"vista": "gestionados"}, "Bandeja · gestionados"), ("reportes:centro", {}, None),
            ("reportes:estadisticas", {}, "Estadísticas y proyección"), ("usuarios", {}, "Usuarios"),
            ("personal:empleado_lista", {}, "Personal"), ("notificaciones", {}, None), ("asistencia:tablero", {}, "Asistencia (tablero)"),
        ]
        for nombre, params, medir in pantallas:
            self.pedir(A, "get", reverse(nombre), params, rol="administrador", fase=F, medir=medir)
        self.pedir(A, "get", reverse("tablero:ventas"), {}, rol="administrador", fase=F, HTTP_HX_REQUEST="true",
                   medir="Tablero · actualización HTMX (cada 30 s)")
        if self.base:
            self.pedir(A, "get", reverse("ventas:base_detalle", args=[self.base.pk]), rol="administrador", fase=F, medir="Detalle de base")
            self.pedir(A, "get", reverse("ventas:base_excel", args=[self.base.pk]), {"gestion": "1"}, rol="administrador", fase=F,
                       medir="Excel de la base con gestión")
        for pk in self.vendedores:
            self.pedir(A, "get", reverse("ventas:hoja_vendedor", args=[pk]), rol="administrador", fase=F)
        from apps.reportes.catalogo import REPORTES

        for codigo, rep in REPORTES.items():
            params = {"mes": mes, "desde": f"{mes}-01", "hasta": timezone.localdate().isoformat()}
            if "vendedor" in rep.obligatorios:
                params["vendedor"] = self.vendedores[0]
            if "empleado" in rep.obligatorios:
                params["empleado"] = self.vendedores[0]
            self.pedir(A, "get", reverse("reportes:reporte", args=[codigo.lower()]), params, rol="administrador", fase=F,
                       medir=f"Reporte {rep.nombre}" if rep.modulo in ("VENTAS", "CIERRE") else None)
            if rep.modulo in ("VENTAS", "CIERRE"):
                self.pedir(A, "get", reverse("reportes:reporte_pdf", args=[codigo.lower()]), params, rol="administrador", fase=F,
                           medir=f"PDF {rep.nombre}")
        # El encargado ve el tablero y el panel; el vendedor no.
        e = self.clientes[self.encargados[0]]
        for nombre in ("cierre:panel", "tablero:ventas", "ventas:bases_seguimiento", "reportes:centro"):
            self.pedir(e, "get", reverse(nombre), rol="encargado", fase=F)
        self.d.paso(F, f"El administrador revisó {len(pantallas)} pantallas, las 40 hojas de producción y los {len(REPORTES)} reportes (con PDF de ventas y cierre).")

    # ------------------------------------------------------------------ 10. ¿cuadran los números?
    def fase_consistencia(self):
        F = "10. Consistencia de datos"
        cerrado_bd = GestionCierre.objects.filter(resultado="VENTA_CERRADA").aggregate(s=Sum("valor_venta"))["s"] or D(0)
        hoja_bd = RegistroProduccion.objects.filter(estado="VENTA_CERRADA", hoja__anio=self.anio, hoja__mes=self.mes) \
            .aggregate(s=Sum("valor_venta"))["s"] or D(0)
        panel = cierre_servicios.panel_comercial(self.anio, self.mes, self.admin_usuario)
        tablero = cierre_servicios.tablero_ventas(self.anio, self.mes, self.admin_usuario)
        r = self.pedir(self.admin, "get", reverse("tablero:ventas"), rol="administrador", fase=F)
        tv = r.context["totales"]["total_vendido"] if r is not None else None
        self.d.paso(F, f"Total vendido — gestiones de cierre: ${cerrado_bd} · hojas de producción: ${hoja_bd} · panel comercial: "
                       f"${panel.totales['total_vendido']} · tablero (pantalla): ${tv}.")
        if not (cerrado_bd == hoja_bd == panel.totales["total_vendido"] == tv):
            self.d.hallazgo("CRÍTICO", F, "El total vendido no coincide entre cierre, hoja de producción, panel y tablero",
                            f"cierre={cerrado_bd} hoja={hoja_bd} panel={panel.totales['total_vendido']} tablero={tv}")
        n_cerradas = GestionCierre.objects.filter(resultado="VENTA_CERRADA").count()
        if panel.totales["ventas_cerradas"] != n_cerradas:
            self.d.hallazgo("ALTO", F, f"Panel: {panel.totales['ventas_cerradas']} ventas cerradas; en cierre hay {n_cerradas}")

        # Cada DAMI con su fila en la hoja y el mismo estado.
        desfasados = []
        # (Un borrador anulado antes de enviarse nunca tuvo fila en la hoja: no se cuenta.)
        for f in FormularioDAMI.objects.exclude(estado=Estado.BORRADOR).exclude(estado=Estado.ANULADO, enviado_en__isnull=True) \
                .select_related("registro_produccion"):
            reg = getattr(f, "registro_produccion", None)
            esperado = {"ENVIADO": "PENDIENTE", "EN_GESTION": "PENDIENTE"}.get(f.estado, f.estado)
            if reg is None or reg.estado != esperado:
                desfasados.append(f"{f} {f.estado} → hoja {getattr(reg, 'estado', 'SIN FILA')}")
            elif f.estado == "VENTA_CERRADA" and reg.valor_venta != f.gestion.valor_venta:
                desfasados.append(f"{f} valor {f.gestion.valor_venta} ≠ hoja {reg.valor_venta}")
        if desfasados:
            self.d.hallazgo("CRÍTICO", F, f"{len(desfasados)} DAMI no coinciden con la hoja de producción", "; ".join(desfasados[:8]))

        # Ranking del tablero ordenado y podio correcto.
        montos = [f.total_vendido for f in tablero["filas"]]
        if montos != sorted(montos, reverse=True):
            self.d.hallazgo("ALTO", F, "El tablero de ventas no está ordenado de mayor a menor")
        if len(tablero["filas"]) != len(self.vendedores):
            self.d.hallazgo("MEDIO", F, f"El tablero muestra {len(tablero['filas'])} vendedores; hay {len(self.vendedores)}")

        # Metas: porcentaje = vendido / meta.
        malas = []
        for fila in panel.filas:
            meta = self.metas.get(fila.vendedor.pk)
            if meta and fila.monto_meta != meta:
                malas.append(f"{fila.vendedor}: meta {fila.monto_meta} ≠ {meta}")
            if meta and fila.porcentaje_meta is not None and abs(D(fila.porcentaje_meta) - fila.total_vendido * 100 / meta) > 1:
                malas.append(f"{fila.vendedor}: {fila.porcentaje_meta}% ≠ {fila.total_vendido * 100 / meta:.0f}%")
        if malas:
            self.d.hallazgo("ALTO", F, "Las metas o su porcentaje no cuadran en el panel", "; ".join(malas[:6]))
        if 999999 in {m.vendedor_id for m in cierre_servicios.MetaMensual.objects.all()} if hasattr(cierre_servicios, "MetaMensual") else False:
            self.d.hallazgo("ALTO", F, "Se guardó una meta para un vendedor inexistente")

        # Contactos y DAMI.
        sacados = RegistroBase.objects.filter(estado="DAMI_SACADO").count()
        enviados_contacto = len(self.dami_enviados)
        con_base = FormularioDAMI.objects.filter(base_comercial__isnull=False).count()
        self.d.paso(F, f"Contactos «DAMI sacado»: {sacados}; DAMI enviados desde contactos: {enviados_contacto}; "
                       f"DAMI con su base comercial registrada: {con_base} de {FormularioDAMI.objects.exclude(estado=Estado.BORRADOR).count()}.")
        self.d.paso(F, f"DAMI desde contactos sin base: {getattr(self, 'sin_base', 0)}; contactos que no pasaron solos a "
                       f"«DAMI sacado»: {getattr(self, 'sin_marca_auto', 0)}.")
        if getattr(self, "sin_base", 0) or getattr(self, "sin_marca_auto", 0):
            self.d.hallazgo("MEDIO", F, "Los DAMI llenados desde «Mis contactos» no quedan vinculados a su base comercial ni al contacto",
                            f"{self.sin_base} DAMI sin base y {self.sin_marca_auto} contactos sin marcar automáticamente.")

        # Avisos a los encargados por cada DAMI.
        for pk in self.encargados:
            avisos = Notificacion.objects.filter(destinatario__empleado__pk=pk, tipo="FORMULARIO_DAMI").count()
            if avisos < len(self.dami_enviados) - 1:
                self.d.hallazgo("MEDIO", F, f"El encargado {pk} recibió {avisos} avisos de DAMI; se enviaron {len(self.dami_enviados)}")
                break
        sin_numero = Notificacion.objects.filter(mensaje__contains="4111111111111111").exists() or \
            Notificacion.objects.filter(titulo__contains="4111111111111111").exists()
        if sin_numero:
            self.d.hallazgo("CRÍTICO", F, "Un aviso contiene el número completo de una tarjeta")

        # Auditoría con autor.
        sin_autor = RegistroCambio.objects.filter(tabla__icontains="gestioncierre", usuario_id__isnull=True).count()
        con = RegistroCambio.objects.filter(tabla__icontains="gestioncierre").count()
        self.d.paso(F, f"Auditoría: {con} cambios de cierre registrados, {sin_autor} sin autor.")
        if con == 0:
            self.d.hallazgo("MEDIO", F, "No se registró auditoría de los cierres")
        elif sin_autor:
            self.d.hallazgo("MEDIO", F, f"{sin_autor} cambios de cierre quedaron en la auditoría sin usuario")
        # Números de tarjeta: nunca en texto plano.
        from apps.ventas.models import TarjetaDAMI

        campos = [c.name for c in TarjetaDAMI._meta.get_fields() if getattr(c, "get_internal_type", lambda: "")() in ("CharField", "TextField")]
        planos = sum(TarjetaDAMI.objects.filter(**{f"{c}__contains": "4111111111111111"}).count() for c in campos)
        if planos:
            self.d.hallazgo("CRÍTICO", F, f"{planos} tarjetas guardadas con el número en texto plano")

    # ------------------------------------------------------------------ 11. barrido de pantallas por rol
    def fase_barrido_permisos(self):
        F = "11. Barrido de pantallas por rol"
        quien = {"administrador": self.admin, "encargado": self.clientes[self.encargados[0]],
                 "vendedor": self.clientes[self.vendedores[0]]}
        omitir = {"logout", "sw.js", "admin"}
        rutas = []

        def recorrer(patrones, prefijo="", ns=""):
            for p in patrones:
                if isinstance(p, URLResolver):
                    if str(p.pattern).startswith("admin"):
                        continue
                    recorrer(p.url_patterns, prefijo + str(p.pattern), p.namespace or ns)
                elif isinstance(p, URLPattern) and not p.pattern.converters and "<" not in str(p.pattern):
                    ruta = "/" + prefijo + str(p.pattern)
                    if not any(o in ruta for o in omitir):
                        rutas.append(ruta)

        recorrer(get_resolver().url_patterns)
        errores = 0
        for ruta in sorted(set(rutas)):
            for rol, cliente in quien.items():
                r = self.pedir(cliente, "get", ruta, rol=rol, fase=F, esperado=None)
                if r is None:
                    errores += 1
                    continue
                self.d.matriz[ruta][rol] = r.status_code
        # Lo que un vendedor no debería abrir
        sensibles = ["/cierre/", "/cierre/metas/", "/cierre/panel/", "/tablero/", "/usuarios/", "/usuarios/nuevo/",
                     "/personal/empleados/", "/personal/empleados/nuevo/", "/nomina/", "/ventas/hoja-de-produccion/vendedores/",
                     "/ventas/bases/seguimiento/", "/ventas/bases/contactos/"]
        for ruta in sensibles:
            s = self.d.matriz.get(ruta, {}).get("vendedor")
            if s == 200:
                self.d.hallazgo("ALTO", F, f"Un vendedor puede abrir {ruta}")
        for ruta in ["/usuarios/nuevo/", "/personal/empleados/nuevo/", "/nomina/"]:
            if self.d.matriz.get(ruta, {}).get("encargado") == 200:
                self.d.hallazgo("MEDIO", F, f"Un encargado de cierre puede abrir {ruta}")
        self.d.paso(F, f"Se abrieron {len(set(rutas))} pantallas sin parámetros con cada rol ({len(set(rutas)) * 3} peticiones); "
                       f"{errores} terminaron en error 500.")

    # ------------------------------------------------------------------ informe
    def imprimir(self):
        d = self.d
        orden = {"CRÍTICO": 0, "ALTO": 1, "MEDIO": 2, "BAJO": 3}
        lineas = ["# Diagnóstico · flujo real CALLFLOW", "",
                  f"Fecha simulada: {timezone.localdate():%d/%m/%Y} · 1 administrador, {len(getattr(self, 'vendedores', []))} vendedores, "
                  f"{len(getattr(self, 'encargados', []))} encargados de cierre.",
                  f"Peticiones HTTP: {sum(d.peticiones.values())} ({dict(d.peticiones)}). Códigos: {dict(sorted(d.estados.items()))}.", "",
                  "## Lo que se hizo", ""]
        for fase, texto in d.pasos:
            lineas.append(f"- **{fase}:** {texto}")
        lineas += ["", f"## Hallazgos ({len(d.hallazgos)})", ""]
        if not d.hallazgos:
            lineas.append("Sin hallazgos.")
        for sev, fase, titulo, detalle in sorted(d.hallazgos, key=lambda h: orden.get(h[0], 9)):
            lineas.append(f"- **[{sev}]** ({fase}) {titulo}")
            if detalle:
                lineas.append("  ```\n  " + detalle.strip().replace("\n", "\n  ")[:1500] + "\n  ```")
        lineas += ["", "## Rendimiento (consultas SQL y tiempo por pantalla, base de prueba local)", "",
                   "| Pantalla | Rol | Consultas | ms |", "|---|---|---:|---:|"]
        for pantalla, rol, q, ms in sorted(d.metricas, key=lambda m: -m[2]):
            lineas.append(f"| {pantalla} | {rol} | {q} | {ms} |")
        lineas += ["", "## Acceso por rol (código HTTP al abrir cada pantalla)", "", "| Ruta | Admin | Encargado | Vendedor |", "|---|---|---|---|"]
        for ruta in sorted(d.matriz):
            m = d.matriz[ruta]
            lineas.append(f"| `{ruta}` | {m.get('administrador', '')} | {m.get('encargado', '')} | {m.get('vendedor', '')} |")
        texto = "\n".join(lineas)
        print("\n" + texto)
        destino = os.environ.get("REPORTE_FLUJO")
        if destino:
            with open(destino, "w", encoding="utf-8") as archivo:
                archivo.write(texto)
