"""
Errors raised by the warehouse services.
"""

from django.core.exceptions import ValidationError

from rest_framework import status
from rest_framework.exceptions import APIException


class InsufficientStock(APIException):
    """
    An order asks for more portions than the warehouse can cover.

    Raised inside the order-creation transaction so the half-built order rolls
    back; DRF renders it as 409 with the per-dish shortfalls so a client can
    tell the customer exactly what to remove.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "insufficient_stock"

    def __init__(self, shortfalls):
        self.shortfalls = list(shortfalls)
        names = ", ".join(sorted({s["name"] for s in self.shortfalls})) or "some items"
        super().__init__({"message": f"Not enough stock for: {names}.", "items": self.shortfalls})


class UnitMismatch(ValidationError):
    """A quantity was given in a unit of the wrong dimension (kg for a litre item)."""


class InventoryError(ValidationError):
    """Any other rule violation (inactive item, missing recipe, ...)."""
