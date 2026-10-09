"""Configuração do AgroClima Guiné-Bissau (todas as opções vêm de variáveis de ambiente)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# Render (e plataformas semelhantes) indicam o domínio público nesta variável
if env("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(env("RENDER_EXTERNAL_HOSTNAME"))
    CSRF_TRUSTED_ORIGINS.append("https://" + env("RENDER_EXTERNAL_HOSTNAME"))
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "dashboard",
]

MIDDLEWARE = [
    "dashboard.views.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("POSTGRES_DB", "agroclima"),
        "USER": env("POSTGRES_USER", "agroclima"),
        "PASSWORD": env("POSTGRES_PASSWORD", "agroclima"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }
}

# Cache partilhada entre workers (mapids do GEE, séries temporais, estatísticas)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": env("CACHE_DIR", str(BASE_DIR / ".cache")),
        "TIMEOUT": 60 * 60 * 6,
    }
}

LANGUAGE_CODE = "pt"
TIME_ZONE = "Africa/Bissau"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# GDAL/GEOS em instalações locais fora do Docker (opcional)
if env("GDAL_LIBRARY_PATH"):
    GDAL_LIBRARY_PATH = env("GDAL_LIBRARY_PATH")
if env("GEOS_LIBRARY_PATH"):
    GEOS_LIBRARY_PATH = env("GEOS_LIBRARY_PATH")

# ---------------------------------------------------------------------------
# Google Earth Engine
# ---------------------------------------------------------------------------
GEE_PROJECT = env("GEE_PROJECT", "")
GEE_SERVICE_ACCOUNT_KEY = env("GEE_SERVICE_ACCOUNT_KEY", "")  # caminho para o JSON da conta de serviço

# Limites administrativos usados quando a base PostGIS ainda não tem regiões
GEE_ADMIN_L1_ASSET = env("GEE_ADMIN_L1_ASSET", "FAO/GAUL/2015/level1")
GEE_ADMIN_L2_ASSET = env("GEE_ADMIN_L2_ASSET", "FAO/GAUL/2015/level2")
GEE_COUNTRY_FIELD = env("GEE_COUNTRY_FIELD", "ADM0_NAME")
GEE_COUNTRY_NAME = env("GEE_COUNTRY_NAME", "Guinea-Bissau")

# Períodos de referência (climatologia)
RAIN_BASELINE = (int(env("RAIN_BASELINE_START", 1991)), int(env("RAIN_BASELINE_END", 2020)))
VEG_BASELINE = (int(env("VEG_BASELINE_START", 2001)), int(env("VEG_BASELINE_END", 2020)))

# Limites de segurança
MAX_AOI_KM2 = float(env("MAX_AOI_KM2", 50000))
MAX_PERIOD_DAYS = int(env("MAX_PERIOD_DAYS", 3660))
MAX_TIMELAPSE_FRAMES = int(env("MAX_TIMELAPSE_FRAMES", 36))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
