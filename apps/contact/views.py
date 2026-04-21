import logging

from django.conf import settings
from django.core.mail import EmailMessage

from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from drf_spectacular.utils import extend_schema

from .models import ContactMessage
from .serializers import ContactMessageCreateSerializer

logger = logging.getLogger(__name__)

# Public email the visitor-facing form submits to. Configurable via env so
# the address can change without a code deploy.
CONTACT_RECIPIENT = getattr(settings, "CONTACT_RECIPIENT_EMAIL", "info@telos.ge")


class ContactFormThrottle(AnonRateThrottle):
    """10 submissions per IP per hour — enough for genuine retries after
    typos, tight enough to blunt a bored spammer."""

    scope = "contact"
    rate = "10/hour"


def _client_ip(request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@extend_schema(tags=["Contact"])
class ContactCreateView(generics.CreateAPIView):
    """Public POST endpoint for the /contact form on aimenu.ge."""

    serializer_class = ContactMessageCreateSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ContactFormThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Silent drop for honeypot hits — respond with the same 201 the happy
        # path returns so the bot gets no signal it was caught.
        if data.get("website"):
            return Response(
                {"success": True, "message": "Message received."},
                status=status.HTTP_201_CREATED,
            )

        msg = ContactMessage.objects.create(
            first_name=data["first_name"],
            last_name=data.get("last_name", ""),
            email=data["email"],
            phone=data.get("phone", ""),
            topic=data.get("topic", ""),
            message=data["message"],
            ip_address=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
            locale=(request.META.get("HTTP_ACCEPT_LANGUAGE", "") or "")[:8],
        )

        # Fire the email. If SMTP is down we still return success — the
        # record is persisted so the team can recover from admin.
        try:
            subject = f"[aimenu.ge contact] {data.get('topic') or 'New message'} from {data['first_name']}"
            body = (
                f"Name: {data['first_name']} {data.get('last_name', '')}\n"
                f"Email: {data['email']}\n"
                f"Phone: {data.get('phone', '') or '—'}\n"
                f"Topic: {data.get('topic', '') or '—'}\n"
                f"Submitted: {msg.created_at:%Y-%m-%d %H:%M %Z}\n"
                f"IP: {msg.ip_address or '—'}\n"
                "\n"
                "Message:\n"
                f"{data['message']}\n"
            )
            # Reply-To set to the submitter so replies from info@telos.ge
            # go straight back to the person who filled the form.
            EmailMessage(
                subject=subject,
                body=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[CONTACT_RECIPIENT],
                reply_to=[data["email"]],
            ).send(fail_silently=False)
        except Exception:
            logger.exception("Failed to dispatch contact email for id=%s", msg.id)

        return Response(
            {"success": True, "message": "Message received.", "id": msg.id},
            status=status.HTTP_201_CREATED,
        )
