"""
Development settings for restaurant_platform project.
"""
from .base import *

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1', '.localhost']

# CORS - Allow all origins in development
CORS_ALLOW_ALL_ORIGINS = True

# Security settings for development
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False

# Email backend for development
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Disable SSL for MinIO in development
AWS_S3_USE_SSL = False

# Debug toolbar (optional)
if DEBUG:
    INSTALLED_APPS += ['django_extensions']

    # Slower throttle rates for development testing
    REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] = {
        'anon': '1000/hour',
        'user': '10000/hour',
        'burst': '600/minute',
        'auth': '100/minute',
    }
