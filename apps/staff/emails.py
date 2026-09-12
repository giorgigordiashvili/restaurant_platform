"""Staff invitation email: HTML + text, linking to the customer site's accept page."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def accept_url(invitation) -> str:
    lang = getattr(invitation.restaurant, "default_language", "ka") or "ka"
    prefix = "" if lang == "ka" else f"/{lang}"  # the site's default locale has no prefix
    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}{prefix}/staff/accept/{invitation.token}"


def admin_url(restaurant) -> str:
    return f"https://{restaurant.slug}.{settings.ADMIN_DOMAIN}/tenant-admin/"


def send_invitation_email(invitation) -> bool:
    restaurant = invitation.restaurant
    context = {
        "invitation": invitation,
        "restaurant": restaurant,
        "role": invitation.role.get_display_name(),
        "invited_by": invitation.invited_by,
        "message": invitation.message,
        "accept_url": accept_url(invitation),
        "expires_at": invitation.expires_at,
    }
    subject = f"You're invited to join {restaurant.name} on AiMenu"
    text = render_to_string("staff/email/invitation.txt", context)
    html = render_to_string("staff/email/invitation.html", context)
    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [invitation.email])
    msg.attach_alternative(html, "text/html")
    try:
        msg.send(fail_silently=True)
        return True
    except Exception:  # pragma: no cover - mail backends vary
        logger.exception("staff invitation email failed for %s", invitation.email)
        return False
