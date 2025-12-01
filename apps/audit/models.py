"""
Audit log models for tracking sensitive operations.
"""

from django.db import models

from apps.core.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    """
    Audit log for tracking sensitive operations.
    """

    ACTION_CHOICES = [
        ("login", "User Login"),
        ("logout", "User Logout"),
        ("login_failed", "Failed Login"),
        ("password_change", "Password Change"),
        ("password_reset", "Password Reset"),
        ("user_create", "User Created"),
        ("user_update", "User Updated"),
        ("user_delete", "User Deleted"),
        ("staff_add", "Staff Added"),
        ("staff_remove", "Staff Removed"),
        ("order_create", "Order Created"),
        ("order_update", "Order Updated"),
        ("order_cancel", "Order Cancelled"),
        ("payment_collect", "Payment Collected"),
        ("settings_update", "Settings Updated"),
        ("data_export", "Data Exported"),
    ]

    # Actor
    user = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    user_email = models.EmailField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    # Context
    restaurant = models.ForeignKey(
        "tenants.Restaurant", on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )

    # Action
    action = models.CharField(max_length=50, choices=ACTION_CHOICES, db_index=True)

    # Target
    target_model = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=100, blank=True)

    # Details
    description = models.TextField(blank=True)
    changes = models.JSONField(default=dict, blank=True)

    # Request metadata
    request_method = models.CharField(max_length=10, blank=True)
    request_path = models.CharField(max_length=500, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "audit_logs"
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["restaurant", "created_at"]),
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["target_model", "target_id"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.user_email} at {self.created_at}"
