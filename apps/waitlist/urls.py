from django.urls import path

from .views import PublicJoinInfoView, PublicStatusView

app_name = "waitlist"

urlpatterns = [
    path("status/<str:token>/", PublicStatusView.as_view(), name="status"),
    path("<slug:slug>/<str:token>/", PublicJoinInfoView.as_view(), name="join"),
]
