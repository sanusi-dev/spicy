"""Django settings for Spicy."""

import os
import sys
from pathlib import Path
from typing import Any

import environ
from django.utils.translation import gettext_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
env.read_env(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = env("SECRET_KEY", default="django-insecure-HjfWKVIxpdgt4NHh8q56GGVTdjDvKeYV32hlsbl1")

DEBUG = env.bool("DEBUG", default=True)
ENABLE_DEBUG_TOOLBAR = env.bool("ENABLE_DEBUG_TOOLBAR", default=False) and "test" not in sys.argv

# Dev-only escape hatch: superusers skip Django-admin locks (submitted-document
# locks, hard permission blocks, readonly field lists) while this is on. It is
# tied to DEBUG so it vanishes automatically when the product goes live. The
# restriction code itself stays untouched.
SPICY_DEV_ADMIN_BYPASS = DEBUG

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.forms",
]

THIRD_PARTY_APPS = [
    "allauth",  # allauth account/registration management
    "allauth.account",
    "django_htmx",
    "django_vite",
    "django_celery_beat",
    "django_hugeicons_stroke",
]

PROJECT_APPS = [
    "apps.users.apps.UserConfig",
    "apps.settings.apps.SettingsConfig",
    "apps.inventory.apps.InventoryConfig",
    "apps.menu.apps.MenuConfig",
    "apps.web",
    "apps.payments.apps.PaymentsConfig",
    "apps.staff.apps.StaffConfig",
    "apps.orders.apps.OrdersConfig",
    "apps.accounting.apps.AccountingConfig",
    "apps.reports.apps.ReportsConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + PROJECT_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.web.middleware.MessagesMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if ENABLE_DEBUG_TOOLBAR:
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INSTALLED_APPS.append("debug_toolbar")
    INTERNAL_IPS = ["127.0.0.1"]

if DEBUG:
    INSTALLED_APPS.append("django_browser_reload")
    MIDDLEWARE.append("django_browser_reload.middleware.BrowserReloadMiddleware")

if DEBUG:
    INSTALLED_APPS.append("django_watchfiles")

ROOT_URLCONF = "spicy.urls"

# Cached template loaders are disabled under DEBUG so edits show without restart.
_DEFAULT_LOADERS = [
    "django.template.loaders.filesystem.Loader",
    "django.template.loaders.app_directories.Loader",
]

_CACHED_LOADERS = [("django.template.loaders.cached.Loader", _DEFAULT_LOADERS)]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "templates",
        ],
        "OPTIONS": {
            "builtins": [
                "django_hugeicons_stroke.templatetags.hugeicons_stroke",
            ],
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.context_processors.project_meta",
                "apps.web.context_processors.csrf_settings",
                "apps.web.context_processors.inventory_navigation",
            ],
            "loaders": _DEFAULT_LOADERS if DEBUG else _CACHED_LOADERS,
        },
    },
]

WSGI_APPLICATION = "spicy.wsgi.application"

FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

# Database

if "DATABASE_URL" in env:
    DATABASES = {"default": env.db()}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DJANGO_DATABASE_NAME", default="spicy"),
            "USER": env("DJANGO_DATABASE_USER", default="postgres"),
            "PASSWORD": env("DJANGO_DATABASE_PASSWORD", default="***"),
            "HOST": env("DJANGO_DATABASE_HOST", default="localhost"),
            "PORT": env("DJANGO_DATABASE_PORT", default="5432"),
        }
    }

# Auth and Login

AUTH_USER_MODEL = "users.CustomUser"
SESSION_COOKIE_NAME = "spicy_sessionid"
CSRF_COOKIE_NAME = "spicy_csrftoken"
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "/"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Allauth

ACCOUNT_ADAPTER = "apps.users.adapter.SpicyAccountAdapter"
ACCOUNT_LOGIN_METHODS = {"username"}
ACCOUNT_SIGNUP_FIELDS = ["username*", "password1*", "password2*"]

ACCOUNT_EMAIL_SUBJECT_PREFIX = ""
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_CONFIRM_EMAIL_ON_GET = False
ACCOUNT_UNIQUE_EMAIL = False
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = False
ACCOUNT_LOGIN_BY_CODE_ENABLED = False
ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "phone_number_x"
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_USER_DISPLAY = lambda user: user.get_display_name()  # noqa: E731

ACCOUNT_FORMS = {
    "login": "apps.users.forms.CustomLoginForm",
    "signup": "apps.users.forms.CustomSignupForm",
}

ACCOUNT_EMAIL_VERIFICATION = env("ACCOUNT_EMAIL_VERIFICATION", default="none")

AUTHENTICATION_BACKENDS = (
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
)

# Internationalization

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = False

USE_TZ = True

STATIC_ROOT = BASE_DIR / "static_root"
STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

DJANGO_VITE = {
    "default": {
        "dev_mode": env.bool("DJANGO_VITE_DEV_MODE", default=DEBUG),
        "dev_server_host": env("DJANGO_VITE_HOST", default="localhost"),
        "dev_server_port": env.int("DJANGO_VITE_PORT", default=5173),
        "manifest_path": BASE_DIR / "static" / ".vite" / "manifest.json",
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# Silence UrlField assume_scheme deprecation warnings.
FORMS_URLFIELD_ASSUME_HTTPS = True

SERVER_EMAIL = env("SERVER_EMAIL", default="noreply@localhost:8000")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="sanusio293@gmail.com")

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")

EMAIL_SUBJECT_PREFIX = "[Spicy] "

SITE_ID = 1

# `or` treats an empty REDIS_URL as unset so it falls back instead of yielding an empty URL
REDIS_URL = env("REDIS_URL", default=None) or env("REDIS_TLS_URL", default=None)
if not REDIS_URL:
    REDIS_HOST = env("REDIS_HOST", default="localhost")
    REDIS_PORT = env("REDIS_PORT", default="6379")
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"

DUMMY_CACHE = {
    "BACKEND": "django.core.cache.backends.dummy.DummyCache",
}
REDIS_CACHE = {
    "BACKEND": "django.core.cache.backends.redis.RedisCache",
    "LOCATION": REDIS_URL,
}
CACHES = {
    "default": DUMMY_CACHE if DEBUG else REDIS_CACHE,
}

CELERY_BROKER_URL = CELERY_RESULT_BACKEND = REDIS_URL
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Add tasks to this dict and run `python manage.py bootstrap_celery_tasks` to create them
SCHEDULED_TASKS: dict[str, Any] = {}


PROJECT_METADATA = {
    "NAME": gettext_lazy("Spicy"),
    "URL": "http://localhost:8000",
    "DESCRIPTION": gettext_lazy(
        "Spicy is a lightweight POS for restaurant order management. Cashiers "
        "enter all orders and payments; waiters use physical dockets and do not "
        "access the system."
    ),
    "IMAGE": "https://upload.wikimedia.org/wikipedia/commons/2/20/PEO-pegasus_black.svg",
    "KEYWORDS": "SaaS, django",
    "CONTACT_EMAIL": "sanusio293@gmail.com",
}

USE_HTTPS_IN_ABSOLUTE_URLS = env.bool("USE_HTTPS_IN_ABSOLUTE_URLS", default=False)

ADMINS = ["sanusio293@gmail.com"]


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": '[{asctime}] {levelname} "{name}" {message}',
            "style": "{",
            "datefmt": "%d/%b/%Y %H:%M:%S",  # match Django server time format
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL", default="INFO"),
        },
        "apps": {
            "handlers": ["console"],
            "level": env("SPICY_LOG_LEVEL", default="INFO"),
        },
        "pegasus": {
            "handlers": ["console"],
            "level": env("PEGASUS_LOG_LEVEL", default="DEBUG"),
        },
    },
}
