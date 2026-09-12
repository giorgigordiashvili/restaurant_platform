"""Notification mails for share requests (informational; never a capability)."""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _admin_url(restaurant):
    return f"https://{restaurant.slug}.{settings.ADMIN_DOMAIN}/tenant-admin/venues/venuesharerequest/"


def _recipients(restaurant):
    emails = [restaurant.owner.email] if restaurant.owner_id and restaurant.owner.email else []
    if restaurant.email and restaurant.email not in emails:
        emails.append(restaurant.email)
    return emails


def _send(subject, body, recipients):
    if not recipients:
        return
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=True)
    except Exception:  # pragma: no cover - mail backends vary
        logger.exception("venue email failed: %s", subject)


def send_share_request_email(req):
    sender, target = req.from_restaurant, req.to_restaurant
    body = (
        f"{sender.name} wants to share tables with {target.name} on AiMenu.\n\n"
        + (f"Venue name: {req.venue_name}\n" if req.venue_name else "")
        + (f"Message: {req.message}\n" if req.message else "")
        + f"\nReview the request in your admin: {_admin_url(target)}\n"
        f"It expires on {req.expires_at:%Y-%m-%d}.\n"
    )
    _send(f"{sender.name} wants to share tables with {target.name}", body, _recipients(target))


def send_share_response_email(req):
    sender, target = req.from_restaurant, req.to_restaurant
    verb = "accepted" if req.status == req.STATUS_ACCEPTED else "declined"
    body = (
        f"{target.name} {verb} your request to share tables.\n\n"
        f"Manage the shared venue in your admin: {_admin_url(sender)}\n"
    )
    _send(f"{target.name} {verb} your table-sharing request", body, _recipients(sender))
