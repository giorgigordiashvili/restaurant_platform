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

        # Lazy import to avoid circular imports
        from apps.tenants.models import Restaurant

        # First, check for X-Restaurant header (useful for API testing and direct API calls)
        # In Django, HTTP headers are stored in META with HTTP_ prefix
        restaurant_slug = request.META.get("HTTP_X_RESTAURANT")
        if restaurant_slug:
            try:
                restaurant = Restaurant.objects.get(slug=restaurant_slug, is_active=True)
                request.restaurant = restaurant
                request.is_dashboard = True
            except Restaurant.DoesNotExist:
                # Restaurant not found via header
                pass
        else:
            # Fall back to subdomain-based resolution
            host = request.get_host().split(":")[0]  # Remove port if present
            main_domain = getattr(settings, "MAIN_DOMAIN", "localhost")

            # Check if it's a subdomain
            if host.endswith(f".{main_domain}"):
                subdomain = host.replace(f".{main_domain}", "")

                if subdomain and subdomain not in self.EXCLUDED_SUBDOMAINS:
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
    Works with both function-based views and class-based view methods.
    Also works with methods like get_queryset that don't receive request directly.
    """
    from functools import wraps

    from django.http import HttpRequest

    from rest_framework.request import Request

    @wraps(view_func)
    def _wrapped_view(*args, **kwargs):
        request = None

        # First, check args for request object
        for arg in args:
            # Check if this argument is a request object (DRF Request or Django HttpRequest)
            if isinstance(arg, (Request, HttpRequest)):
                request = arg
                break
            # Check if this is a class-based view instance with a request attribute
            # Only check for real request objects, not just any hasattr
            view_request = getattr(arg, "request", None)
            if view_request is not None and isinstance(view_request, (Request, HttpRequest)):
                request = view_request
                break

        # Fallback for testing: if no real request found, check first arg for restaurant attr
        if request is None and args:
            first_arg = args[0]
            # Check if the attribute is explicitly set (not auto-created by Mock)
            if "restaurant" in getattr(first_arg, "__dict__", {}):
                request = first_arg

        if request is None:
            raise Http404("Restaurant not found")

        # For DRF Request objects, the restaurant is on the underlying _request
        restaurant = getattr(request, "restaurant", None)
        if restaurant is None and isinstance(request, Request):
            # Only check _request for actual DRF Request objects, not Mock objects
            restaurant = getattr(request._request, "restaurant", None)

        if not restaurant:
            raise Http404("Restaurant not found")
        return view_func(*args, **kwargs)

    return _wrapped_view
