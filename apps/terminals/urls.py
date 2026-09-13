from django.urls import path

from .views import BogCallbackView, PayPageView, TbcCallbackView

app_name = "terminals"

urlpatterns = [
    path("bog/callback/", BogCallbackView.as_view(), name="bog-callback"),
    path("tbc/callback/", TbcCallbackView.as_view(), name="tbc-callback"),
    path("pay/<uuid:tx_id>/", PayPageView.as_view(), name="pay-page"),
]
