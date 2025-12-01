"""
Production settings for restaurant_platform project.
"""

import dj_database_url
from decouple import Csv, config

from .base import *  # noqa: F401, F403

DEBUG = False

# Redis is optional - use local memory cache if not available
REDIS_URL = config("REDIS_URL", default=None)
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }

# Allow multiple hosts
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="", cast=Csv())
ALLOWED_HOSTS = [h for h in ALLOWED_HOSTS if h]

# Always allow these domains
ALLOWED_HOSTS.extend([
    "groot.ge",
    ".groot.ge",  # All subdomains
    ".ondigitalocean.app",  # All DigitalOcean app domains
])

# Also add APP_DOMAIN if set by DigitalOcean
APP_DOMAIN = config("APP_DOMAIN", default="")
if APP_DOMAIN and APP_DOMAIN not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(APP_DOMAIN)

# Support for DigitalOcean App Platform DATABASE_URL
DATABASE_URL = config("DATABASE_URL", default=None)
if DATABASE_URL:
    DATABASES["default"] = dj_database_url.config(  # noqa: F405
        default=DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=True,
    )

# Security settings for production
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# CSRF trusted origins for DigitalOcean
CSRF_TRUSTED_ORIGINS: list = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
# Filter out empty strings
CSRF_TRUSTED_ORIGINS = [origin for origin in CSRF_TRUSTED_ORIGINS if origin]

# CORS - Restricted in production
CORS_ALLOW_ALL_ORIGINS = False

# Use proper email backend in production
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

# MinIO / S3 settings for production
if USE_MINIO:  # noqa: F405
    AWS_S3_USE_SSL = config("MINIO_USE_SSL", default=True, cast=bool)
    MINIO_ENDPOINT = config("MINIO_ENDPOINT", default="")
    if MINIO_ENDPOINT:
        # For external S3-compatible storage (DigitalOcean Spaces, etc.)
        if MINIO_ENDPOINT.startswith("http"):
            AWS_S3_ENDPOINT_URL = MINIO_ENDPOINT
        else:
            protocol = "https" if AWS_S3_USE_SSL else "http"
            AWS_S3_ENDPOINT_URL = f"{protocol}://{MINIO_ENDPOINT}"

        # Custom domain for public access
        AWS_S3_CUSTOM_DOMAIN = config("MINIO_EXTERNAL_ENDPOINT", default=MINIO_ENDPOINT)

# More restrictive logging in production
LOGGING["root"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["django"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["apps"]["level"] = "INFO"  # noqa: F405
