from django.urls import path

from .views import (
    AdjustView,
    GiftCardDetailView,
    GiftCardListView,
    LookupView,
    RedeemView,
    ResendView,
    SellView,
    SummaryView,
    VoidView,
)

urlpatterns = [
    path("summary/", SummaryView.as_view(), name="giftcards-summary"),
    path("", GiftCardListView.as_view(), name="giftcards-list"),
    path("sell/", SellView.as_view(), name="giftcards-sell"),
    path("lookup/", LookupView.as_view(), name="giftcards-lookup"),
    path("redeem/", RedeemView.as_view(), name="giftcards-redeem"),
    path("<uuid:card_id>/", GiftCardDetailView.as_view(), name="giftcards-detail"),
    path("<uuid:card_id>/void/", VoidView.as_view(), name="giftcards-void"),
    path("<uuid:card_id>/adjust/", AdjustView.as_view(), name="giftcards-adjust"),
    path("<uuid:card_id>/resend/", ResendView.as_view(), name="giftcards-resend"),
]
