"""
Audit log models for tracking sensitive operations.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    """
    Audit log for tracking sensitive operations.
    """

    ACTION_CHOICES = [
        ("login", _("User Login")),
        ("logout", _("User Logout")),
        ("login_failed", _("Failed Login")),
        ("password_change", _("Password Change")),
        ("password_reset", _("Password Reset")),
        ("user_create", _("User Created")),
        ("user_update", _("User Updated")),
        ("user_delete", _("User Deleted")),
        ("staff_add", _("Staff Added")),
        ("staff_remove", _("Staff Removed")),
        ("order_create", _("Order Created")),
        ("order_update", _("Order Updated")),
        ("order_cancel", _("Order Cancelled")),
        ("payment_collect", _("Payment Collected")),
        ("settings_update", _("Settings Updated")),
        ("data_export", _("Data Exported")),
        ("stock_receive", _("Stock Received")),
        ("stock_adjust", _("Stock Adjusted")),
        ("stock_waste", _("Stock Written Off")),
        ("employee_meal", _("Employee Meal Recorded")),
        ("recipe_update", _("Recipe Updated")),
        ("warehouse_toggle", _("Warehouse Toggled")),
        ("shift_open", _("Cash Shift Opened")),
        ("shift_close", _("Cash Shift Closed")),
        ("cash_movement", _("Cash Paid In / Out")),
        ("refund", _("Refund Issued")),
        ("order_discount", _("Order Discount")),
        ("item_comp", _("Item Comped")),
        ("item_void", _("Item Voided")),
        ("order_split", _("Order Split")),
        ("order_move", _("Order Moved")),
        ("menu_availability", _("Dish availability changed")),
        ("po_create", _("Purchase order created")),
        ("po_send", _("Purchase order sent")),
        ("po_receive", _("Purchase order received")),
        ("po_cancel", _("Purchase order cancelled")),
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
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["restaurant", "created_at"]),
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["target_model", "target_id"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.user_email} at {self.created_at}"
