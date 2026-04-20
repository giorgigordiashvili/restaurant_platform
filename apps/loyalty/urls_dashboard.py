from django.urls import path

from . import views

app_name = "loyalty_dashboard"

urlpatterns = [
    path("programs/", views.DashboardProgramListCreateView.as_view(), name="programs"),
    path("programs/<uuid:pk>/", views.DashboardProgramDetailView.as_view(), name="program-detail"),
    path("redeem/validate/", views.DashboardValidateView.as_view(), name="redeem-validate"),
    path("redeem/confirm/", views.DashboardConfirmView.as_view(), name="redeem-confirm"),
]
