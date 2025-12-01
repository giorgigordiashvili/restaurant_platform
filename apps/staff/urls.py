"""
Public staff URLs (invitation acceptance).
"""

from django.urls import path

from .views import AcceptInvitationView, InvitationDetailsView

app_name = "staff"

urlpatterns = [
    # Accept must come before <str:token> to avoid "accept" being captured as a token
    path("invitations/accept/", AcceptInvitationView.as_view(), name="accept-invitation"),
    path("invitations/<str:token>/", InvitationDetailsView.as_view(), name="invitation-details"),
]
