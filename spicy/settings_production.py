# flake8: noqa: F405
from .settings import *  # noqa: F401,F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

USE_HTTPS_IN_ABSOLUTE_URLS = True

ADMINS = ["sanusio293@gmail.com"]
