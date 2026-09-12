"""
Tenant / platform admin: when the browser has not picked a language through
the switcher yet, use the signed-in user's ``preferred_language`` instead of
the Accept-Language guess (Georgian staff often run English browsers).
"""

from django.conf import settings
from django.utils import translation


class AdminUserLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.supported = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request):
        if request.path.startswith(("/admin/", "/tenant-admin/")):
            cookie = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
            user = getattr(request, "user", None)
            preferred = getattr(user, "preferred_language", None) if getattr(user, "is_authenticated", False) else None
            if not cookie and preferred in self.supported:
                translation.activate(preferred)
                request.LANGUAGE_CODE = preferred
        return self.get_response(request)
