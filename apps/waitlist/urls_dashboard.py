from django.urls import path

from .views import (
    EntryCancelView,
    EntryDetailView,
    EntryLeftView,
    EntryListView,
    EntryNoShowView,
    EntryNotifyView,
    EntrySeatView,
    EstimateView,
    ReservationToWaitlistView,
    SettingsView,
    SummaryView,
)

urlpatterns = [
    path("summary/", SummaryView.as_view(), name="waitlist-summary"),
    path("settings/", SettingsView.as_view(), name="waitlist-settings"),
    path("estimate/", EstimateView.as_view(), name="waitlist-estimate"),
    path("entries/", EntryListView.as_view(), name="waitlist-entries"),
    path("entries/<uuid:entry_id>/", EntryDetailView.as_view(), name="waitlist-entry"),
    path("entries/<uuid:entry_id>/notify/", EntryNotifyView.as_view(), name="waitlist-notify"),
    path("entries/<uuid:entry_id>/seat/", EntrySeatView.as_view(), name="waitlist-seat"),
    path("entries/<uuid:entry_id>/left/", EntryLeftView.as_view(), name="waitlist-left"),
    path("entries/<uuid:entry_id>/no-show/", EntryNoShowView.as_view(), name="waitlist-no-show"),
    path("entries/<uuid:entry_id>/cancel/", EntryCancelView.as_view(), name="waitlist-cancel"),
    path(
        "reservations/<uuid:reservation_id>/to-waitlist/",
        ReservationToWaitlistView.as_view(),
        name="waitlist-from-reservation",
    ),
]
