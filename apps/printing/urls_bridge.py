from django.urls import path

from .views_bridge import BridgeJobDoneView, BridgeJobFailedView, BridgeNextJobView, BridgePingView

app_name = "print_bridge"

urlpatterns = [
    path("ping/", BridgePingView.as_view(), name="ping"),
    path("jobs/next/", BridgeNextJobView.as_view(), name="next"),
    path("jobs/<uuid:id>/done/", BridgeJobDoneView.as_view(), name="done"),
    path("jobs/<uuid:id>/failed/", BridgeJobFailedView.as_view(), name="failed"),
]
