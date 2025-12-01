"""
Dashboard staff management URLs (tenant-scoped).
"""

from django.urls import path

from .views import (
    StaffDetailView,
    StaffInvitationCancelView,
    StaffInvitationsListView,
    StaffInviteView,
    StaffListView,
    StaffRoleDetailView,
    StaffRolesListView,
)

app_name = "staff_dashboard"

urlpatterns = [
    # Staff members
    path("", StaffListView.as_view(), name="staff-list"),
    path("<uuid:id>/", StaffDetailView.as_view(), name="staff-detail"),
    path("invite/", StaffInviteView.as_view(), name="staff-invite"),
    # Invitations
    path("invitations/", StaffInvitationsListView.as_view(), name="invitations-list"),
    path("invitations/<uuid:id>/cancel/", StaffInvitationCancelView.as_view(), name="invitation-cancel"),
    # Roles
    path("roles/", StaffRolesListView.as_view(), name="roles-list"),
    path("roles/<uuid:id>/", StaffRoleDetailView.as_view(), name="role-detail"),
]
