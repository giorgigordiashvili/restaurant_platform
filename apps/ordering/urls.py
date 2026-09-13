from django.urls import path

from .views import DomainCheckView, PublicConfigView, PublicDeliveryQuoteView, PublicSlotsView, RestaurantByDomainView

app_name = "ordering"

urlpatterns = [
    path("by-domain/", RestaurantByDomainView.as_view(), name="by-domain"),
    path("domains/check/", DomainCheckView.as_view(), name="domain-check"),
    path("<slug:slug>/config/", PublicConfigView.as_view(), name="config"),
    path("<slug:slug>/slots/", PublicSlotsView.as_view(), name="slots"),
    path("<slug:slug>/delivery-quote/", PublicDeliveryQuoteView.as_view(), name="delivery-quote"),
]
