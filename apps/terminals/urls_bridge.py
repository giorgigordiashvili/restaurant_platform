from django.urls import path

from .views import BridgeNextJobView, BridgePingView, BridgeResultView

app_name = "terminal_bridge"

urlpatterns = [
    path("ping/", BridgePingView.as_view(), name="ping"),
    path("jobs/next/", BridgeNextJobView.as_view(), name="next"),
    path("jobs/<uuid:tx_id>/result/", BridgeResultView.as_view(), name="result"),
]
