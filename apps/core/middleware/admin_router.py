"""
Middleware to route admin requests to the appropriate admin site.

Redirects /admin/ to /tenant-admin/ when on a restaurant subdomain.
"""

from django.http import HttpResponseRedirect


class TenantAdminRouterMiddleware:
    """
    Routes /admin/ requests to /tenant-admin/ when on a restaurant subdomain.

    This middleware must come after TenantMiddleware which sets is_tenant_admin.

    URL Structure:
    - aimenu.ge/admin/ -> Default Django admin (superadmins)
    - pizza-palace.aimenu.ge/admin/ -> Redirects to /tenant-admin/ (restaurant staff)
    - pizza-palace.aimenu.ge/tenant-admin/ -> Tenant admin with unfold UI
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if this is a tenant admin request trying to access /admin/
        if getattr(request, "is_tenant_admin", False) and request.path.startswith("/admin/"):
            # Redirect to tenant-admin with the same path suffix
            new_path = request.path.replace("/admin/", "/tenant-admin/", 1)

            # Preserve query string if any
            if request.META.get("QUERY_STRING"):
                new_path += "?" + request.META["QUERY_STRING"]

            return HttpResponseRedirect(new_path)

        return self.get_response(request)
