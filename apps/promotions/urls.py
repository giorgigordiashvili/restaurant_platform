from django.urls import path

from .views import PublicValidateCodeView

app_name = "promotions"

urlpatterns = [
    path("<slug:slug>/validate/", PublicValidateCodeView.as_view(), name="validate-code"),
]
