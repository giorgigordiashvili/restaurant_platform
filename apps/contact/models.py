from django.db import models


class ContactMessage(models.Model):
    """
    One submission from the /contact form on aimenu.ge.

    Persisted so that mail-server flakes, typos in the recipient, or a busy
    inbox can't lose a message. `is_handled` lets the team mark messages
    they've replied to without deleting the record.
    """

    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    topic = models.CharField(max_length=160, blank=True)
    message = models.TextField()

    # Request metadata — useful for triage but not displayed to the submitter.
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    locale = models.CharField(max_length=8, blank=True, help_text="Locale of the visitor (ka/en/ru)")

    is_handled = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "contact_messages"
        ordering = ["-created_at"]
        verbose_name = "Contact message"
        verbose_name_plural = "Contact messages"

    def __str__(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip() or self.email
        return f"{name} — {self.created_at:%Y-%m-%d %H:%M}"
