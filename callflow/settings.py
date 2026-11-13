"""
Configuración de CALLFLOW.

Las credenciales y datos sensibles se leen de variables de entorno
(archivo .env en la raíz del proyecto). Nunca se escriben en el código.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _cargar_env(ruta: Path) -> None:
    """Carga un archivo .env simple (CLAVE=valor) sin dependencias externas."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env(BASE_DIR / ".env")


def env(clave: str, defecto: str | None = None) -> str:
    valor = os.environ.get(clave, defecto)
    if valor is None:
        raise RuntimeError(f"Falta la variable de entorno {clave} (revise el archivo .env)")
    return valor


SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG", "False").lower() in ("1", "true", "si", "yes")
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
# Orígenes desde los que se aceptan formularios (POST) cuando se entra por IP o por otro puerto,
# p. ej. http://192.168.100.83:8000 al abrir la app desde la otra computadora de la red.
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Núcleo
    "apps.core",
    # 4.1 Módulo administrativo
    "apps.personal",
    "apps.documentos",
    "apps.asistencia",
    "apps.inventario",
    "apps.chips",
    "apps.agenda",
    # 4.2 Módulo de nómina
    "apps.nomina",
    # 4.3 Módulo de ventas
    "apps.ventas",
    # 4.4 Módulo de cierre de ventas
    "apps.cierre",
    # 4.5 Reportes y exportación
    "apps.reportes",
    # Departamento de TI: equipos, hoja de vida y equipos conectados
    "apps.ti",
    # Mensajería interna entre TI, administradores y supervisores
    "apps.mensajes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.UsuarioAuditoriaMiddleware",
    "apps.core.middleware.CambioClaveObligatorioMiddleware",
    "apps.agenda.middleware.AvisosAgendaMiddleware",
    "apps.ti.middleware.ActividadEquiposMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.push.PushMiddleware",
]

ROOT_URLCONF = "callflow.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.navegacion",
            ],
        },
    },
]

WSGI_APPLICATION = "callflow.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "callflow"),
        "USER": env("DB_USER", "callflow_app"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", "5432"),
        "ATOMIC_REQUESTS": True,
        # connect_timeout: si la base está en otra computadora y no responde, falla en segundos en vez de colgarse.
        "OPTIONS": {"options": "-c timezone=America/Guayaquil", "connect_timeout": int(env("DB_CONNECT_TIMEOUT", "5"))},
    }
}

AUTH_USER_MODEL = "core.Usuario"

# Clave Fernet para cifrar los números de tarjeta del DAMI (ver apps/ventas/tarjetas.py).
CLAVE_TARJETAS = env("CLAVE_TARJETAS", "")
AUTHENTICATION_BACKENDS = ["apps.core.backends.UsuarioNombreBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Jornada laboral de la empresa: única fuente para asistencia (horario estándar),
# agenda (no se agenda fuera de este horario) y avisos. ISO: 1 = lunes … 7 = domingo.
JORNADA_LABORAL = {
    "inicio": "09:00",
    "fin": "18:00",
    "dias": [1, 2, 3, 4, 5],
}

# «Finalizar día»: si pasados estos minutos después de la hora de salida la persona no finalizó
# su día, se crea una incidencia SIN_SALIDA y se avisa a los administradores.
ASISTENCIA_MARGEN_SALIDA_MINUTOS = 60
# Hasta cuántos días atrás se buscan jornadas sin salida (evita alertar por registros antiguos).
ASISTENCIA_DIAS_REVISION_SALIDA = 7

# Almuerzo: duración permitida y margen. Si al volver (iniciar sesión) se pasó más que la tolerancia,
# se crea la novedad «Exceso en almuerzo» con todos los minutos de más y se avisa a los administradores.
ASISTENCIA_ALMUERZO_MINUTOS = 60
ASISTENCIA_ALMUERZO_TOLERANCIA_MINUTOS = 5
# Breaks: minutos de gracia después del fin antes de contar el regreso como tarde (0 = sin gracia).
ASISTENCIA_BREAK_TOLERANCIA_MINUTOS = 0

# TI: minutos sin actividad para dejar de mostrar a alguien como «conectado», y si CALLFLOW está detrás de
# un proxy (Nginx, IIS) que envía la IP real del equipo en X-Forwarded-For (solo activarlo en ese caso).
TI_MINUTOS_CONECTADO = 5
TI_PROXY_CONFIABLE = env("TI_PROXY_CONFIABLE", "False").lower() in ("1", "true", "si", "yes")

# Reporte de reposiciones de chips: quiénes firman (nombre, cargo), en ese orden.
CHIPS_FIRMAS_REPORTE = [
    ("Raul Tapia", "Dueño"),
    ("Diego Mendoza", "Gerente de Talento Humano y Marketing"),
    ("Marlon Rosero de Mendoza", "Coordinador"),
]

LANGUAGE_CODE = "es-ec"
TIME_ZONE = "America/Guayaquil"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "inicio"
LOGOUT_REDIRECT_URL = "login"

MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))

# Límite de archivos adjuntos (debe coincidir con la restricción de la tabla documentos_documento)
TAMANO_MAXIMO_ARCHIVO = 10 * 1024 * 1024  # 10 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = TAMANO_MAXIMO_ARCHIVO

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# Notificaciones push del navegador (Web Push). Generarlas con: python manage.py generar_claves_push --guardar
# Sin claves no se envían push; los avisos dentro del sistema funcionan igual.
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", "")
VAPID_CONTACTO = env("VAPID_CONTACTO", "mailto:soporte@callflow.local")

# --------------------------------------------------------------------------- registro de errores (TI › Servidor)
# Los errores (nivel ERROR o más) van a LOGS_DIR/errores.jsonl, una línea JSON por error, con rotación:
# 5 archivos de 5 MB. TI los ve en «Servidor». Durante las pruebas se escriben en una carpeta temporal.
import sys  # noqa: E402
import tempfile  # noqa: E402

PROBANDO = len(sys.argv) > 1 and sys.argv[1] == "test"
LOGS_DIR = Path(tempfile.mkdtemp(prefix="callflow-logs-")) if PROBANDO else Path(env("LOGS_DIR", str(BASE_DIR / "logs")))
LOGS_DIR.mkdir(parents=True, exist_ok=True)
# Carpeta de las copias de seguridad (scripts/respaldar_bd.ps1), para mostrar el último respaldo.
CARPETA_RESPALDOS = Path(env("CARPETA_RESPALDOS", str(BASE_DIR / "respaldos")))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "apps.ti.registro_errores.FormatoJSON"},
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "consola": {"class": "logging.StreamHandler", "formatter": "simple", "level": "WARNING"},
        "errores": {
            "class": "logging.handlers.RotatingFileHandler", "filename": str(LOGS_DIR / "errores.jsonl"),
            "maxBytes": 5 * 1024 * 1024, "backupCount": 5, "encoding": "utf-8", "delay": True,
            "formatter": "json", "level": "ERROR",
        },
    },
    "root": {"handlers": ["consola", "errores"], "level": "WARNING"},
    # Sin manejadores propios: lo de Django sube a la raíz (sin esto, en depuración cada aviso sale dos veces).
    "loggers": {"django": {"handlers": [], "level": "INFO", "propagate": True}},
}
