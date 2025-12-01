"""
Production settings for restaurant_platform project.
"""
from .base import *

DEBUG = False

ALLOWED_HOSTS = config('ALLOWED_HOSTS', cast=Csv())

# Security settings for production
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# CORS - Restricted in production
CORS_ALLOW_ALL_ORIGINS = False

# Use proper email backend in production
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# Enable SSL for MinIO in production
AWS_S3_USE_SSL = config('MINIO_USE_SSL', default=True, cast=bool)

# More restrictive logging in production
LOGGING['root']['level'] = 'WARNING'
LOGGING['loggers']['django']['level'] = 'WARNING'
LOGGING['loggers']['apps']['level'] = 'INFO'
