from django.urls import path

from .views import UnsubscribeView

app_name = "crm"

urlpatterns = [path("unsubscribe/<str:token>/", UnsubscribeView.as_view(), name="unsubscribe")]
