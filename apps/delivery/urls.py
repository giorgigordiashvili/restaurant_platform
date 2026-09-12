from django.urls import path

from .glovo.views import GlovoCancelWebhookView, GlovoMenuFeedView, GlovoOrderWebhookView
from .wolt.views import WoltOrderWebhookView

app_name = "delivery"

urlpatterns = [
    path("glovo/orders/", GlovoOrderWebhookView.as_view(), name="glovo-orders"),
    path("glovo/orders/<str:order_id>/cancel/", GlovoCancelWebhookView.as_view(), name="glovo-cancel"),
    path("glovo/menu/<uuid:link_id>/<str:token>/", GlovoMenuFeedView.as_view(), name="glovo-menu"),
    path("wolt/orders/", WoltOrderWebhookView.as_view(), name="wolt-orders"),
]
