from django.urls import path

from .views import PublicBalanceView, PublicCardView

app_name = "giftcards"

urlpatterns = [
    path("card/<str:token>/", PublicCardView.as_view(), name="card"),
    path("<slug:slug>/balance/", PublicBalanceView.as_view(), name="balance"),
]
