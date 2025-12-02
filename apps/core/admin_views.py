"""
Custom admin views for superadmin functionality.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.views.decorators.http import require_POST


@staff_member_required
@require_POST
def set_simulated_restaurant(request):
    """
    Set or clear the simulated restaurant in session.

    POST Parameters:
        restaurant_id: UUID of restaurant to simulate, or empty to clear

    Only superusers can use this feature.
    """
    if not request.user.is_superuser:
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/admin/"))

    restaurant_id = request.POST.get("restaurant_id", "").strip()

    if restaurant_id:
        # Validate the restaurant exists
        from apps.tenants.models import Restaurant

        try:
            Restaurant.objects.get(id=restaurant_id)
            request.session["admin_simulated_restaurant"] = restaurant_id
        except (Restaurant.DoesNotExist, ValueError):
            # Invalid ID, clear simulation
            request.session.pop("admin_simulated_restaurant", None)
    else:
        # Clear simulation
        request.session.pop("admin_simulated_restaurant", None)

    # Redirect back to the referring page, or admin index
    referer = request.META.get("HTTP_REFERER")
    if referer:
        return HttpResponseRedirect(referer)
    return redirect("admin:index")
