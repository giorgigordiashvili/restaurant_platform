"""
URL configuration for audit app (admin endpoints).
"""

from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    # Admin endpoints (platform-wide)
    path("", views.AdminAuditLogListView.as_view(), name="admin-list"),
    path("<uuid:id>/", views.AdminAuditLogDetailView.as_view(), name="admin-detail"),
]
