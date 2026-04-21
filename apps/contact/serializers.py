from rest_framework import serializers

from .models import ContactMessage


class ContactMessageCreateSerializer(serializers.ModelSerializer):
    """Shape accepted by POST /api/v1/contact/."""

    # Honeypot: humans never fill this. Any non-empty value silently drops
    # the submission on the view layer — spam bots autofill every field.
    website = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = ContactMessage
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "topic",
            "message",
            "website",
        ]
        extra_kwargs = {
            "last_name": {"required": False, "allow_blank": True},
            "phone": {"required": False, "allow_blank": True},
            "topic": {"required": False, "allow_blank": True},
        }

    def validate_first_name(self, value: str) -> str:
        v = value.strip()
        if len(v) < 1:
            raise serializers.ValidationError("First name is required.")
        return v

    def validate_message(self, value: str) -> str:
        v = value.strip()
        if len(v) < 10:
            raise serializers.ValidationError("Message must be at least 10 characters.")
        if len(v) > 5000:
            raise serializers.ValidationError("Message must be at most 5000 characters.")
        return v
