from django.urls import path

from .views import ItemUnavailableView, OrderPromoCodeView, PromotionListView, UnavailableListView

urlpatterns = [
    path("", PromotionListView.as_view(), name="promotions-list"),
    path("unavailable/", UnavailableListView.as_view(), name="promotions-unavailable"),
    path("items/<uuid:item_id>/86/", ItemUnavailableView.as_view(), name="promotions-item-86"),
    path("orders/<uuid:order_id>/promo-code/", OrderPromoCodeView.as_view(), name="promotions-order-code"),
]
