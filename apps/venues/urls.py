from django.urls import path

from .views import VenueDetailView, VenueMenuView, VenueTableValidateView

app_name = "venues"

urlpatterns = [
    # Literal route first: "validate" is a reserved venue slug.
    path("validate/<str:code>/", VenueTableValidateView.as_view(), name="validate"),
    path("<slug:slug>/", VenueDetailView.as_view(), name="detail"),
    path("<slug:slug>/menu/", VenueMenuView.as_view(), name="menu"),
]
