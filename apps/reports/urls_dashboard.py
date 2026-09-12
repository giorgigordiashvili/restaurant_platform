from django.urls import path

from .views import ReportView

app_name = "reports_dashboard"

urlpatterns = [
    path("<slug:key>/", ReportView.as_view(), name="report"),
]
