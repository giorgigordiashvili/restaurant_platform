"""
Tenant middleware for subdomain-based multi-tenancy.
"""

from django.conf import settings
from django.http import Http404


class TenantMiddleware:
    """
    Middleware to resolve tenant (restaurant) from subdomain.

    Example:
        - myrestaurant.domain.ge -> restaurant with slug 'myrestaurant'
        - api.domain.ge -> no tenant (main API)
        - www.domain.ge -> no tenant (main site)
    """

    EXCLUDED_SUBDOMAINS = ["www", "api", "admin", "static", "media"]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.restaurant = None
        request.is_dashboard = False

        host = request.get_host().split(":")[0]  # Remove port if present
        main_domain = getattr(settings, "MAIN_DOMAIN", "localhost")

        # Check if it's a subdomain
        if host.endswith(f".{main_domain}"):
            subdomain = host.replace(f".{main_domain}", "")

            if subdomain and subdomain not in self.EXCLUDED_SUBDOMAINS:
                # Lazy import to avoid circular imports
                from apps.tenants.models import Restaurant

                try:
                    restaurant = Restaurant.objects.get(slug=subdomain, is_active=True)
                    request.restaurant = restaurant
                    request.is_dashboard = True
                except Restaurant.DoesNotExist:
                    # Restaurant not found - will be handled by views
                    pass

        response = self.get_response(request)
        return response


def get_current_restaurant(request):
    """
    Helper function to get the current restaurant from request.
    """
    return getattr(request, "restaurant", None)


def require_restaurant(view_func):
    """
    Decorator that requires a restaurant to be set in the request.
    """
    from functools import wraps

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not getattr(request, "restaurant", None):
            raise Http404("Restaurant not found")
        return view_func(request, *args, **kwargs)

    return _wrapped_view
