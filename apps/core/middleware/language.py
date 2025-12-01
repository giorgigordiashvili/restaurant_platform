"""
Language middleware for API multi-language support.
"""
from django.conf import settings
from django.utils import translation


class APILanguageMiddleware:
    """
    Middleware to activate language based on:
    1. Query parameter (?lang=en)
    2. User's preferred_language setting
    3. Accept-Language header
    4. Default language (Georgian)
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.supported_languages = [lang[0] for lang in settings.LANGUAGES]

    def __call__(self, request):
        # Priority 1: Query parameter
        lang = request.GET.get('lang')

        # Priority 2: User preference (if authenticated)
        if not lang and hasattr(request, 'user') and request.user.is_authenticated:
            lang = getattr(request.user, 'preferred_language', None)

        # Priority 3: Accept-Language header
        if not lang:
            lang = self._get_language_from_header(request)

        # Validate and activate language
        if lang and lang in self.supported_languages:
            translation.activate(lang)
            request.LANGUAGE_CODE = lang
        else:
            translation.activate(settings.LANGUAGE_CODE)
            request.LANGUAGE_CODE = settings.LANGUAGE_CODE

        response = self.get_response(request)

        # Add Content-Language header to response
        response['Content-Language'] = request.LANGUAGE_CODE

        return response

    def _get_language_from_header(self, request):
        """
        Parse Accept-Language header and return the first supported language.

        Example header: "ka,en-US;q=0.9,en;q=0.8,ru;q=0.7"
        """
        accept_language = request.META.get('HTTP_ACCEPT_LANGUAGE', '')

        if not accept_language:
            return None

        # Parse and sort by quality value
        languages = []
        for lang_entry in accept_language.split(','):
            parts = lang_entry.strip().split(';')
            lang_code = parts[0].split('-')[0].lower()  # Get base language code

            # Get quality value (default is 1.0)
            q = 1.0
            if len(parts) > 1:
                try:
                    q = float(parts[1].split('=')[1])
                except (ValueError, IndexError):
                    pass

            if lang_code in self.supported_languages:
                languages.append((lang_code, q))

        if languages:
            # Sort by quality value (descending) and return highest
            languages.sort(key=lambda x: x[1], reverse=True)
            return languages[0][0]

        return None
