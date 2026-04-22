"""URL routes for referral endpoints."""

from django.urls import path

from .views import ReferralSummaryView, ReferredUsersView, WalletHistoryView

app_name = "referrals"

urlpatterns = [
    path("me/", ReferralSummaryView.as_view(), name="me"),
    path("history/", WalletHistoryView.as_view(), name="history"),
    path("referred/", ReferredUsersView.as_view(), name="referred"),
]
