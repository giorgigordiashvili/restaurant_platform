from django.urls import path

from .views_dashboard import (
    VenueLeaveView,
    VenueSectionDetailView,
    VenueSectionsView,
    VenueShareRequestAcceptView,
    VenueShareRequestCancelView,
    VenueShareRequestCreateView,
    VenueShareRequestDeclineView,
    VenueStateView,
    VenueTableDeactivateView,
    VenueTableDetailView,
    VenueTablesView,
)

app_name = "venue_dashboard"

urlpatterns = [
    path("", VenueStateView.as_view(), name="state"),
    path("tables/", VenueTablesView.as_view(), name="tables"),
    path("tables/<uuid:id>/", VenueTableDetailView.as_view(), name="table-detail"),
    path("tables/<uuid:id>/deactivate/", VenueTableDeactivateView.as_view(), name="table-deactivate"),
    path("sections/", VenueSectionsView.as_view(), name="sections"),
    path("sections/<uuid:id>/", VenueSectionDetailView.as_view(), name="section-detail"),
    path("requests/", VenueShareRequestCreateView.as_view(), name="request-create"),
    path("requests/<uuid:id>/accept/", VenueShareRequestAcceptView.as_view(), name="request-accept"),
    path("requests/<uuid:id>/decline/", VenueShareRequestDeclineView.as_view(), name="request-decline"),
    path("requests/<uuid:id>/cancel/", VenueShareRequestCancelView.as_view(), name="request-cancel"),
    path("leave/", VenueLeaveView.as_view(), name="leave"),
]
