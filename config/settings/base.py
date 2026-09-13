"""
Base Django settings for restaurant_platform project.
"""

from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config("SECRET_KEY")

# Main domain for subdomain tenant resolution
MAIN_DOMAIN = config("MAIN_DOMAIN", default="localhost")

# Domain the Django admin is served from. Restaurant staff reach their own
# admin at <slug>.ADMIN_DOMAIN, so this is also the suffix the on-demand TLS
# check (apps/core/views.py::tls_check) is willing to issue certificates for.
ADMIN_DOMAIN = config("ADMIN_DOMAIN", default="localhost")

# Application definition
# Unfold must come BEFORE django.contrib.admin for its templates to load
UNFOLD_APPS = [
    "unfold",
    "unfold.contrib.filters",
]

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Required by django-allauth; we pin SITE_ID=1 below.
    "django.contrib.sites",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "parler",
    "django_celery_beat",
    "storages",
    # Social auth (Google + Facebook). Allauth handles provider-token
    # verification; dj-rest-auth exposes DRF endpoints that mint simplejwt
    # tokens in the same {access, refresh, user} shape as our password login.
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.facebook",
    "dj_rest_auth",
    "dj_rest_auth.registration",
]

SITE_ID = 1

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.tenants",
    "apps.staff",
    "apps.menu",
    "apps.tables",
    "apps.orders",
    "apps.reservations",
    "apps.loyalty",
    "apps.payments",
    "apps.favorites",
    "apps.audit",
    "apps.contact",
    "apps.reviews",
    "apps.referrals",
    "apps.venues",
    "apps.inventory",
    "apps.reports",
    "apps.printing",
    "apps.fiscal",
    "apps.delivery",
    "apps.notifications",
    "apps.promotions",
    "apps.purchasing",
    "apps.timekeeping",
    "apps.crm",
    "apps.ordering",
    "apps.terminals",
    "apps.waitlist",
]

INSTALLED_APPS = UNFOLD_APPS + DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",  # Language switching support
    "corsheaders.middleware.CorsMiddleware",
    "apps.core.middleware.language.APILanguageMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.admin_language.AdminUserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.tenant.TenantMiddleware",
    "apps.core.middleware.admin_router.TenantAdminRouterMiddleware",  # Route tenant admin
    "apps.core.middleware.audit.AuditMiddleware",
    # allauth >= 0.56 requires this middleware to populate request metadata
    # used by its account/socialaccount logic.
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "apps" / "core" / "templates",  # Custom admin templates
            BASE_DIR / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB", default="restaurant_db"),
        "USER": config("POSTGRES_USER", default="restaurant_user"),
        "PASSWORD": config("POSTGRES_PASSWORD", default="restaurant_pass"),
        "HOST": config("POSTGRES_HOST", default="db"),
        "PORT": config("POSTGRES_PORT", default="5432"),
    }
}

# Cache
_REDIS_CACHE_URL = config("REDIS_URL", default="redis://redis:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _REDIS_CACHE_URL,
        # DO-managed Valkey enforces TLS (rediss://) but its URI doesn't
        # carry the ssl_cert_reqs query param the Python redis client
        # expects. Fall through to CERT_NONE: the connection stays
        # encrypted, we just skip CA-verification (DO's internal network
        # + firewall-trusted-sources is the security boundary).
        "OPTIONS": ({"ssl_cert_reqs": "none"} if _REDIS_CACHE_URL.startswith("rediss://") else {}),
    }
}

# Custom User Model
AUTH_USER_MODEL = "accounts.User"

# Password validation with Argon2
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "ka"

LANGUAGES = [
    ("ka", "Georgian"),
    ("en", "English"),
    ("ru", "Russian"),
]

USE_I18N = True
USE_L10N = True

LOCALE_PATHS = [
    BASE_DIR / "locale",
]

# Parler (model translations)
# Keyed by SITE_ID, not None: parler looks the tab list up with
# PARLER_LANGUAGES.get(settings.SITE_ID) and only falls back to the None key
# when no SITE_ID is set. django.contrib.sites (SITE_ID = 1, for allauth) made
# that lookup miss, which silently removed every language tab from the admin.
PARLER_LANGUAGES = {
    SITE_ID: (
        {"code": "ka"},
        {"code": "en"},
        {"code": "ru"},
    ),
    "default": {
        # Fall back through every language, not just Georgian. Staff can now
        # create a row in any language first, so a menu item that only has an
        # English name must still render (and not raise TranslationDoesNotExist)
        # for a Georgian or Russian visitor -- and in admin changelists.
        "fallbacks": ["ka", "en", "ru"],
        "hide_untranslated": False,
    },
}

PARLER_DEFAULT_LANGUAGE_CODE = "ka"

TIME_ZONE = "Asia/Tbilisi"
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "apps.core.throttling.BurstRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "user": "1000/hour",
        "burst": "60/minute",
        "auth": "5/minute",
        # /api/v1/contact/ — public, unauthenticated form. 10 genuine
        # retries per IP per hour is plenty; bots hit the same ceiling.
        "contact": "10/hour",
        # /api/v1/reviews/ scopes — authenticated writes. A real diner
        # writes maybe one review after a meal; anything past 10/hour
        # is the spam shape we want to throttle. Media uploads are per
        # review so 30/hour covers a full 5-image + 1-video batch plus
        # retries. Reports come from restaurant staff; 20/hour is
        # generous without letting a rogue account carpet-bomb.
        "review_create": "10/hour",
        "review_edit": "30/hour",
        "review_media": "30/hour",
        "review_report": "20/hour",
    },
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.custom_exception_handler",
}

# JWT Settings
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.serializers.CustomTokenObtainPairSerializer",
}

# drf-spectacular (Swagger) Settings
SPECTACULAR_SETTINGS = {
    "TITLE": "Restaurant Platform API",
    "DESCRIPTION": "Multi-tenant restaurant order management API with multi-language support",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "TAGS": [
        {"name": "Auth", "description": "Authentication endpoints"},
        {"name": "Users", "description": "User profile management"},
        {"name": "Restaurants", "description": "Restaurant discovery and details"},
        {"name": "Menu", "description": "Menu categories and items"},
        {"name": "Tables", "description": "Table and QR code management"},
        {"name": "Orders", "description": "Order management"},
        {"name": "Group Orders", "description": "Group ordering functionality"},
        {"name": "Reservations", "description": "Table reservations"},
        {"name": "Payments", "description": "Payment processing"},
        {"name": "Venues", "description": "Shared venues: several restaurants serving the same tables"},
        {"name": "Dashboard - Warehouse", "description": "Stock, recipes, waste and buy lists"},
        {"name": "Dashboard", "description": "Restaurant dashboard endpoints"},
    ],
}

# CORS Settings
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:3000", cast=Csv())
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "accept-language",
    "x-restaurant",
]

# Security Settings
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Field encryption key
FIELD_ENCRYPTION_KEY = config("FIELD_ENCRYPTION_KEY", default="dev-encryption-key-32bytes-long!")

# Bank of Georgia (BOG) Payment Manager.
#
# Sandbox and production live on separate hosts — sandbox inserts ``-sandbox``
# before ``.bog.ge`` on all three BOG hosts (oauth2, api, payment). We derive
# defaults from ``BOG_SANDBOX`` so a tester only needs to set the credentials +
# webhook key; production switches in a single env flag.
BOG_SANDBOX = config("BOG_SANDBOX", default=True, cast=bool)
BOG_CLIENT_ID = config("BOG_CLIENT_ID", default="")
BOG_CLIENT_SECRET = config("BOG_CLIENT_SECRET", default="")

_bog_oauth_default = (
    "https://oauth2-sandbox.bog.ge/auth/realms/bog/protocol/openid-connect/token"
    if BOG_SANDBOX
    else "https://oauth2.bog.ge/auth/realms/bog/protocol/openid-connect/token"
)
_bog_api_default = "https://api-sandbox.bog.ge/payments/v1" if BOG_SANDBOX else "https://api.bog.ge/payments/v1"
BOG_OAUTH_URL = config("BOG_OAUTH_URL", default=_bog_oauth_default)
BOG_API_URL = config("BOG_API_URL", default=_bog_api_default)

# Webhook signature verification public key (PEM). Sandbox and production use
# DIFFERENT keys — both are published in the public docs:
#   https://api.bog.ge/docs/sandbox/payments/standard-process/callback
#   https://api.bog.ge/docs/en/payments/standard-process/callback
# We bake the sandbox key in as a default so a tester only has to set this env
# var when going live. Store PEM literally (newlines preserved inside quotes) or
# paste a base64 blob — both are accepted by apps.payments.bog.signatures.
_BOG_SANDBOX_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqczfAuhtxw2iF68kS0Hy
bGSv0ZlDAjsXh6VC8avDl3Vxa9qCn6Pzl37Tl2Z21WodiISLeXdhCtOMTeLNUBeb
CYD31y2/MwnhLYqlCk2bOh29fyPc1iT5Eu/k/1IaNRrK9/UVZaTkhOMeEm+aL4y8
5XsE4UjqftEmwrAdbO2G4cCpuoMC9ZXG9gAdr2BFN6i2Vt9eCen5Poj7E1ik7s8T
GyzploVV0NflhwBGeWnvQANUQGr87gsP5k2JG1z5EwnMybJQ7i3XT726rJMaV6QW
sY5hP72Mtv1I1zL2d9FXm9FWOzbpcXCyxuEBXvqqOHzogri8C7KRRYKyk97Ri7D6
8wIDAQAB
-----END PUBLIC KEY-----"""
BOG_WEBHOOK_PUBLIC_KEY = config(
    "BOG_WEBHOOK_PUBLIC_KEY",
    default=_BOG_SANDBOX_PUBLIC_KEY if BOG_SANDBOX else "",
)

# Amount (GEL) charged for the tokenisation pre-auth during "add card". Must be > 0.
# BOG returns the card metadata on the receipt for this pre-auth; we persist it
# and void the hold in the webhook handler (TODO: wire void endpoint).
BOG_ADD_CARD_AMOUNT = config("BOG_ADD_CARD_AMOUNT", default="1.00")
# Fallback deposit amount (GEL) if a restaurant's reservation_settings doesn't
# specify one. Sandbox only — production deployments should require a real value.
BOG_RESERVATION_DEPOSIT_AMOUNT = config("BOG_RESERVATION_DEPOSIT_AMOUNT", default="10.00")
# Absolute URL BOG will POST webhooks to. Leave blank to derive from the incoming
# request host; set to a public tunnel (e.g. ngrok) when running locally so
# BOG's sandbox can reach the callback.
BOG_WEBHOOK_URL = config("BOG_WEBHOOK_URL", default="")

# ---------------------------------------------------------------------------
# Flitt payment provider (pay.flitt.com) — sibling of BOG. Uses HMAC-SHA1
# signing rather than OAuth2. Split payments require a two-step call:
# charge the master merchant, then POST /api/settlement with a receiver[]
# breakdown after the webhook confirms approval. Restaurants hand us their
# sub-merchant id during onboarding; we never hold their signing secret.
# ---------------------------------------------------------------------------
FLITT_MERCHANT_ID = config("FLITT_MERCHANT_ID", default="")
FLITT_SECRET_KEY = config("FLITT_SECRET_KEY", default="")
FLITT_API_URL = config("FLITT_API_URL", default="https://pay.flitt.com")
# The platform's *own* Flitt sub-merchant id — the receiver for our 5 %
# leg on every split. Configure this once per environment.
FLITT_PLATFORM_SUB_MERCHANT_ID = config("FLITT_PLATFORM_SUB_MERCHANT_ID", default="")
# IP allowlist for Flitt server-callback traffic, documented at
# https://docs.flitt.com/api/payments/callbacks/.
FLITT_ALLOWED_WEBHOOK_IPS = [
    ip.strip()
    for ip in config(
        "FLITT_ALLOWED_WEBHOOK_IPS",
        default="54.154.216.60,3.75.125.89",
    ).split(",")
    if ip.strip()
]

# Platform commission percentage applied to every paid order — both BOG and
# Flitt. Overridable per-restaurant via `Restaurant.platform_commission_percent`
# (superuser-editable only). Keep this as a string; apps.payments.splits
# casts it to Decimal on use to avoid floating-point drift.
PLATFORM_COMMISSION_PERCENT = config("PLATFORM_COMMISSION_PERCENT", default="5")

# Default referral payout percent — credited to the referrer's wallet for every
# paid order their referee places. Overridable per-user via
# UserProfile.referral_percent_override (superuser-editable). Decimal-cast in
# apps.referrals.services to avoid float drift.
REFERRAL_DEFAULT_PERCENT = config("REFERRAL_DEFAULT_PERCENT", default="0.5")

# ---------------------------------------------------------------------------
# Social auth (django-allauth + dj-rest-auth)
# ---------------------------------------------------------------------------
# We skip email verification — JWT app, no transactional email infra yet.
# Email is still required at the User model level; social signups that come
# back without one are rejected by the adapter (see apps.accounts.adapters).
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_AUTHENTICATION_METHOD = "email"

# Auto-link a verified social email onto an existing User — a user who
# originally registered with password + email lands in the same account when
# they later click "Continue with Google". Only applied when the provider
# reports email_verified=true; see apps.accounts.adapters for the guard.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

# Inline provider config — client_id / secret come from env so Django /admin/
# SocialApp rows aren't needed per environment. Keeps credentials in DO's
# encrypted env panel rather than in the application database.
GOOGLE_OAUTH_CLIENT_ID = config("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = config("GOOGLE_OAUTH_CLIENT_SECRET", default="")
FACEBOOK_APP_ID = config("FACEBOOK_APP_ID", default="")
FACEBOOK_APP_SECRET = config("FACEBOOK_APP_SECRET", default="")

# Public origin for links we hand back to the frontend — used by the Facebook
# data-deletion callback to point users at their deletion-status page, and
# by the referral-summary serializer to build shareable /register?ref= URLs.
FRONTEND_BASE_URL = config("FRONTEND_BASE_URL", default="https://aimenu.ge")

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    },
    "facebook": {
        "APP": {
            "client_id": FACEBOOK_APP_ID,
            "secret": FACEBOOK_APP_SECRET,
            "key": "",
        },
        "METHOD": "oauth2",
        "SCOPE": ["email", "public_profile"],
        "FIELDS": ["id", "email", "first_name", "last_name"],
    },
}

SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.SocialAccountAdapter"

# Allauth's auth backend is required alongside Django's default so
# SocialAccount login + password login both resolve against our User model.
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# dj-rest-auth: hand back simplejwt tokens directly (no session cookie, no
# rest-framework legacy tokens). JWT_SERIALIZER shapes the response to match
# CustomTokenObtainPairSerializer so the frontend AuthContext.login() flow
# doesn't branch on "was this password or social?".
REST_AUTH = {
    "USE_JWT": True,
    "JWT_AUTH_HTTPONLY": False,
    "SESSION_LOGIN": False,
    "JWT_AUTH_RETURN_EXPIRATION": False,
    # We're JWT-only — dj-rest-auth's legacy DRF-token path isn't used, but
    # importing dj_rest_auth.models unconditionally asks for a token model.
    # Setting it to None disables the resolve.
    "TOKEN_MODEL": None,
    "USER_DETAILS_SERIALIZER": "apps.accounts.serializers.UserSerializer",
    "JWT_SERIALIZER": "apps.accounts.serializers.SocialJWTSerializer",
}

# Celery Configuration
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://redis:6379/1")
CELERY_RESULT_BACKEND = config("REDIS_URL", default="redis://redis:6379/0")
# DO-managed Valkey speaks TLS on port 25061 (rediss://). The URI DO
# returns doesn't set ssl_cert_reqs, which redis-py requires on rediss
# URLs — map to CERT_NONE (encrypted tunnel, no cert-verify). See the
# CACHES block above for the same pattern on the Django cache client.
if CELERY_BROKER_URL.startswith("rediss://"):
    import ssl as _ssl

    CELERY_BROKER_USE_SSL = {"ssl_cert_reqs": _ssl.CERT_NONE}
if CELERY_RESULT_BACKEND.startswith("rediss://"):
    import ssl as _ssl  # noqa: F811

    CELERY_REDIS_BACKEND_USE_SSL = {"ssl_cert_reqs": _ssl.CERT_NONE}
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# MinIO / S3 Storage Settings
USE_MINIO = config("USE_MINIO", default=True, cast=bool)

if USE_MINIO:
    AWS_ACCESS_KEY_ID = config("MINIO_ACCESS_KEY", default="minioadmin")
    AWS_SECRET_ACCESS_KEY = config("MINIO_SECRET_KEY", default="minioadmin")
    AWS_STORAGE_BUCKET_NAME = config("MINIO_BUCKET_NAME", default="media")
    AWS_S3_ENDPOINT_URL = f"http://{config('MINIO_ENDPOINT', default='minio:9000')}"
    AWS_S3_CUSTOM_DOMAIN = config("MINIO_EXTERNAL_ENDPOINT", default="localhost:9000")
    AWS_S3_USE_SSL = config("MINIO_USE_SSL", default=False, cast=bool)
    AWS_DEFAULT_ACL = "public-read"
    AWS_S3_OBJECT_PARAMETERS = {
        "CacheControl": "max-age=86400",
    }
    AWS_QUERYSTRING_AUTH = False
    DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"

# Email Configuration
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="smtp.example.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@restaurant.ge")

# Django Unfold Configuration (for tenant admin)
UNFOLD = {
    "SITE_TITLE": "Restaurant Dashboard",
    "SITE_HEADER": "Restaurant Dashboard",
    "SITE_SYMBOL": "restaurant",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        "primary": {
            "50": "#eff6ff",
            "100": "#dbeafe",
            "200": "#bfdbfe",
            "300": "#93c5fd",
            "400": "#60a5fa",
            "500": "#3b82f6",
            "600": "#2563eb",
            "700": "#1d4ed8",
            "800": "#1e40af",
            "900": "#1e3a8a",
            "950": "#172554",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
    },
    "SHOW_LANGUAGES": True,
}

# The tenant admin (<slug>.ADMIN_DOMAIN) gets its own copy with a
# module-aware sidebar; keep every key of UNFOLD here too (shallow copy).
UNFOLD_TENANT = {
    **UNFOLD,
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": "apps.core.sidebar.navigation",
    },
}

# Where the staff POS app lives (sidebar links, invitation emails).
POS_BASE_URL = config("POS_BASE_URL", default="https://pos.aimenu.ge")
# Where print bridges and other devices reach this API (shown in setup instructions).
PUBLIC_API_BASE_URL = config("PUBLIC_API_BASE_URL", default="https://admin.aimenu.ge")
PRINT_BRIDGE_LONG_POLL_SECONDS = config("PRINT_BRIDGE_LONG_POLL_SECONDS", default=15, cast=int)
# Fiscal providers: "none" numbers documents locally; add {"code": "dotted.path.Provider"} when a live one exists.
FISCAL_DEFAULT_PROVIDER = config("FISCAL_DEFAULT_PROVIDER", default="none")
FISCAL_PROVIDERS = {}
# Glovo Partners API (one integration token + one webhook auth value per integrator; a link may override).
GLOVO_API_TOKEN = config("GLOVO_API_TOKEN", default="")
GLOVO_WEBHOOK_TOKEN = config("GLOVO_WEBHOOK_TOKEN", default="")
GLOVO_AUTH_SCHEME = config("GLOVO_AUTH_SCHEME", default="")
GLOVO_BASE_URL_STAGE = config("GLOVO_BASE_URL_STAGE", default="https://stageapi.glovoapp.com")
GLOVO_BASE_URL_PROD = config("GLOVO_BASE_URL_PROD", default="https://api.glovoapp.com")
GLOVO_PRICES_IN_MINOR_UNITS = config("GLOVO_PRICES_IN_MINOR_UNITS", default=True, cast=bool)
# Wolt POS integration: OAuth client (one per integrator) or a legacy API key; per-link credentials override.
WOLT_CLIENT_ID = config("WOLT_CLIENT_ID", default="")
WOLT_CLIENT_SECRET = config("WOLT_CLIENT_SECRET", default="")
WOLT_API_KEY = config("WOLT_API_KEY", default="")
WOLT_WEBHOOK_SECRET = config("WOLT_WEBHOOK_SECRET", default="")
WOLT_OAUTH_REDIRECT_URI = config("WOLT_OAUTH_REDIRECT_URI", default="")
WOLT_BASE_URL_PROD = config("WOLT_BASE_URL_PROD", default="https://pos-integration-service.wolt.com")
WOLT_BASE_URL_DEV = config("WOLT_BASE_URL_DEV", default="https://pos-integration-service.development.dev.woltapi.com")
WOLT_AUTH_URL_PROD = config(
    "WOLT_AUTH_URL_PROD", default="https://integrations-authentication-service.wolt.com/oauth2/token"
)
# Notifications: SMS provider (none | console | http | twilio) and Expo push.
SMS_PROVIDER = config("SMS_PROVIDER", default="none")
SMS_FROM = config("SMS_FROM", default="")
SMS_HTTP_URL = config("SMS_HTTP_URL", default="")  # placeholders {to} {text} {from}
SMS_HTTP_METHOD = config("SMS_HTTP_METHOD", default="GET")
SMS_HTTP_HEADERS = config("SMS_HTTP_HEADERS", default="")  # JSON object
SMS_HTTP_BODY = config("SMS_HTTP_BODY", default="")  # JSON template; empty = query-string GET
TWILIO_ACCOUNT_SID = config("TWILIO_ACCOUNT_SID", default="")
TWILIO_AUTH_TOKEN = config("TWILIO_AUTH_TOKEN", default="")
TWILIO_FROM = config("TWILIO_FROM", default="")
EXPO_ACCESS_TOKEN = config("EXPO_ACCESS_TOKEN", default="")
# Embed URLs (YouTube / Vimeo "embed" links) for the onboarding videos shown in the tenant admin guide.
# Courier hand-off (first-party delivery orders) -- per-restaurant keys live on the platform link; these are fallbacks.
WOLT_DRIVE_API_KEY = config("WOLT_DRIVE_API_KEY", default="")
WOLT_DRIVE_VENUE_ID = config("WOLT_DRIVE_VENUE_ID", default="")
WOLT_DRIVE_CLIENT_SECRET = config("WOLT_DRIVE_CLIENT_SECRET", default="")
WOLT_DRIVE_BASE_URL_PROD = config("WOLT_DRIVE_BASE_URL_PROD", default="https://daas-public-api.wolt.com")
WOLT_DRIVE_BASE_URL_DEV = config(
    "WOLT_DRIVE_BASE_URL_DEV", default="https://daas-public-api.development.dev.woltapi.com"
)
GLOVO_ODR_CLIENT_ID = config("GLOVO_ODR_CLIENT_ID", default="")
GLOVO_ODR_CLIENT_SECRET = config("GLOVO_ODR_CLIENT_SECRET", default="")
GLOVO_ODR_VENDOR_ID = config("GLOVO_ODR_VENDOR_ID", default="")
GLOVO_ODR_CALLBACK_SECRET = config("GLOVO_ODR_CALLBACK_SECRET", default="")
GLOVO_ODR_COUNTRY = config("GLOVO_ODR_COUNTRY", default="ge")
GLOVO_ODR_BASE_URL_PROD = config("GLOVO_ODR_BASE_URL_PROD", default="https://ondemand-api-glovoapp.deliveryhero.io")
GLOVO_ODR_BASE_URL_STAGE = config(
    "GLOVO_ODR_BASE_URL_STAGE", default="https://api-infra-eu-central-1.stg.ondemandrider.net"
)
GLOVO_ODR_STS_URL = config("GLOVO_ODR_STS_URL", default="https://sts.deliveryhero.io/oauth2/token")
# TBC Checkout (TPAY) base for pay-by-link terminals (per-terminal keys live on the terminal row).
TBC_TPAY_BASE_URL = config("TBC_TPAY_BASE_URL", default="https://api.tbcbank.ge/v1")
# Custom domains: what a restaurant points its DNS at.
PUBLIC_IP = config("PUBLIC_IP", default="")
SITES_CNAME_TARGET = config("SITES_CNAME_TARGET", default="sites.aimenu.ge")
DELIVERY_GUIDE_VIDEO_WOLT = config("DELIVERY_GUIDE_VIDEO_WOLT", default="")
DELIVERY_GUIDE_VIDEO_GLOVO = config("DELIVERY_GUIDE_VIDEO_GLOVO", default="")
WOLT_AUTH_URL_DEV = config(
    "WOLT_AUTH_URL_DEV", default="https://integrations-authentication-service.development.dev.woltapi.com/oauth2/token"
)

# Logging Configuration
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
