"""
Dashboard URLs for reservations.
"""

from django.urls import path

from . import views

app_name = "reservations_dashboard"

urlpatterns = [
    # Settings
    path("settings/", views.DashboardReservationSettingsView.as_view(), name="settings"),
    # Reservations
    path("", views.DashboardReservationListView.as_view(), name="list"),
    path("create/", views.DashboardReservationCreateView.as_view(), name="create"),
    path("today/", views.DashboardTodayReservationsView.as_view(), name="today"),
    path("upcoming/", views.DashboardUpcomingReservationsView.as_view(), name="upcoming"),
    path("stats/", views.DashboardReservationStatsView.as_view(), name="stats"),
    path("<uuid:pk>/", views.DashboardReservationDetailView.as_view(), name="detail"),
    path("<uuid:pk>/status/", views.DashboardReservationStatusView.as_view(), name="status"),
    path("<uuid:pk>/assign-table/", views.DashboardReservationAssignTableView.as_view(), name="assign-table"),
    # Blocked times
    path("blocked-times/", views.DashboardBlockedTimeListView.as_view(), name="blocked-times-list"),
    path("blocked-times/<uuid:pk>/", views.DashboardBlockedTimeDetailView.as_view(), name="blocked-times-detail"),
]
