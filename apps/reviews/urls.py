"""
URL configuration for the reviews app.
"""

from django.urls import path

from . import views

app_name = "reviews"

urlpatterns = [
    # Public
    path(
        "restaurant/<slug:slug>/",
        views.RestaurantReviewsListView.as_view(),
        name="restaurant-list",
    ),
    path(
        "restaurant/<slug:slug>/stats/",
        views.RestaurantReviewStatsView.as_view(),
        name="restaurant-stats",
    ),
    # Me
    path("mine/", views.MyReviewsListView.as_view(), name="mine"),
    path(
        "eligible-orders/",
        views.EligibleOrdersListView.as_view(),
        name="eligible-orders",
    ),
    # CRUD on a single review
    path("", views.ReviewCreateView.as_view(), name="create"),
    path("<uuid:id>/", views.ReviewDetailView.as_view(), name="detail"),
    # Media attached to a review
    path(
        "<uuid:review_id>/media/",
        views.ReviewMediaUploadView.as_view(),
        name="media-upload",
    ),
    path(
        "<uuid:review_id>/media/<uuid:media_id>/",
        views.ReviewMediaDeleteView.as_view(),
        name="media-delete",
    ),
    # Reporting
    path(
        "<uuid:review_id>/report/",
        views.ReviewReportCreateView.as_view(),
        name="report",
    ),
]
