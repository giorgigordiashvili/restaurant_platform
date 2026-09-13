from django.urls import path

from .views import PublicStatementView

app_name = "houseaccounts"

urlpatterns = [path("statement/<str:token>/", PublicStatementView.as_view(), name="statement")]
