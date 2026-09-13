from django.urls import path

from .courier.views import GlovoOdrCallbackView, WoltDriveWebhookView
from .glovo.views import GlovoCancelWebhookView, GlovoMenuFeedView, GlovoOrderWebhookView
from .wolt.views import WoltOrderWebhookView

app_name = "delivery"

urlpatterns = [
    path("glovo/orders/", GlovoOrderWebhookView.as_view(), name="glovo-orders"),
    path("glovo/orders/<str:order_id>/cancel/", GlovoCancelWebhookView.as_view(), name="glovo-cancel"),
    path("glovo/menu/<uuid:link_id>/<str:token>/", GlovoMenuFeedView.as_view(), name="glovo-menu"),
    path("wolt/orders/", WoltOrderWebhookView.as_view(), name="wolt-orders"),
    path("wolt-drive/webhook/", WoltDriveWebhookView.as_view(), name="wolt-drive-webhook"),
    path("glovo-odr/callback/", GlovoOdrCallbackView.as_view(), name="glovo-odr-callback"),
]
