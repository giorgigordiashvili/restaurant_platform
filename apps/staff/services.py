"""
Staff invitations -- one code path for the tenant admin and the dashboard API.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.staff import emails
from apps.staff.models import StaffInvitation, StaffMember

logger = logging.getLogger(__name__)


class InviteError(Exception):
    pass


def invite(restaurant, email, role, *, invited_by, message="", days_valid=7, instance=None) -> StaffInvitation:
    """Create (or fill ``instance``) and email an invitation. Older pending invites for the address are cancelled."""
    email = (email or "").strip().lower()
    if not email:
        raise InviteError("An email address is required.")
    if role.restaurant_id != restaurant.pk:
        raise InviteError("That role belongs to another restaurant.")
    if StaffMember.objects.filter(restaurant=restaurant, user__email__iexact=email, is_active=True).exists():
        raise InviteError("This person is already a staff member here.")
    StaffInvitation.objects.filter(restaurant=restaurant, email=email, status="pending").update(status="cancelled")

    inv = instance or StaffInvitation()
    inv.restaurant = restaurant
    inv.email = email
    inv.role = role
    inv.invited_by = invited_by
    inv.message = message or ""
    inv.status = "pending"
    inv.expires_at = timezone.now() + timedelta(days=days_valid)
    inv.save()
    emails.send_invitation_email(inv)
    _audit(restaurant, invited_by, f"Invited {email} as {role.get_display_name()}", inv)
    return inv


def resend(invitation, *, by) -> StaffInvitation:
    if invitation.status != "pending":
        raise InviteError("Only pending invitations can be resent.")
    import secrets

    invitation.token = secrets.token_urlsafe(48)
    invitation.expires_at = timezone.now() + timedelta(days=7)
    invitation.save(update_fields=["token", "expires_at", "updated_at"])
    emails.send_invitation_email(invitation)
    return invitation


def cancel(invitation, *, by) -> None:
    if invitation.status == "pending":
        invitation.cancel()


def _audit(restaurant, by, description, target):
    try:
        from apps.audit.services import log_action

        log_action(
            "staff_add",
            user=by if getattr(by, "is_authenticated", False) else None,
            restaurant=restaurant,
            description=description,
            target_model=type(target).__name__,
            target_id=str(target.pk),
        )
    except Exception:  # pragma: no cover
        logger.exception("staff audit failed")
