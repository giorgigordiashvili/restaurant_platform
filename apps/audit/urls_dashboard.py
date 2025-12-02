"""
URL configuration for audit app (dashboard endpoints).
"""

from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("", views.DashboardAuditLogListView.as_view(), name="list"),
    path("stats/", views.DashboardAuditLogStatsView.as_view(), name="stats"),
    path("actions/", views.DashboardAuditLogActionsView.as_view(), name="actions"),
    path("export/", views.DashboardAuditLogExportView.as_view(), name="export"),
    path("<uuid:id>/", views.DashboardAuditLogDetailView.as_view(), name="detail"),
]
