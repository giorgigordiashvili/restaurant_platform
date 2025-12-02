"""
Test settings for restaurant_platform project.
Uses SQLite for fast, isolated testing.
"""

from .base import *  # noqa: F401, F403

DEBUG = True

# Use SQLite for testing - faster and no external dependencies
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Use dummy cache for testing - no Redis dependency
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

# Faster password hashing for tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Disable default throttling but keep rates for view-level throttles
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []  # noqa: F405
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "anon": "10000/minute",
    "user": "10000/minute",
    "burst": "10000/minute",
    "auth": "10000/minute",
    "password_reset": "10000/minute",
    "order_creation": "10000/minute",
    "sms": "10000/minute",
}

# Use default exception handler for better error visibility in tests
REST_FRAMEWORK["EXCEPTION_HANDLER"] = "rest_framework.views.exception_handler"  # noqa: F405

# Email backend for testing
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Disable logging during tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "root": {
        "handlers": ["null"],
        "level": "CRITICAL",
    },
}

# Security settings for testing
ALLOWED_HOSTS = ["*"]
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False

# Celery - use synchronous execution for tests
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
