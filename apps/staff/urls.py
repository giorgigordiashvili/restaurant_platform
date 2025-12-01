"""
Public staff URLs (invitation acceptance).
"""

from django.urls import path

from .views import AcceptInvitationView, InvitationDetailsView

app_name = "staff"

urlpatterns = [
    path("invitations/<str:token>/", InvitationDetailsView.as_view(), name="invitation-details"),
    path("invitations/accept/", AcceptInvitationView.as_view(), name="accept-invitation"),
]
