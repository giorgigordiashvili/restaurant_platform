"""
Proxy models: one per report page. They exist only so each report gets its
own sidebar entry, URL and permission row in the tenant admin; no report
ever reads through them (the queries live in apps.reports.queries).
"""

from apps.inventory.models import StockMovement
from apps.orders.models import Order, OrderItem
from apps.payments.models import CashShift
from apps.reservations.models import Reservation
from apps.reviews.models import Review
from apps.timekeeping.models import TimeEntry


class SalesReport(Order):
    class Meta:
        proxy = True
        app_label = "reports"
        verbose_name = "Sales"
        verbose_name_plural = "Sales"


class MenuReport(OrderItem):
    class Meta:
        proxy = True
        app_label = "reports"
        verbose_name = "Menu & dishes"
        verbose_name_plural = "Menu & dishes"


class FoodCostReport(StockMovement):
    class Meta:
        proxy = True
        app_label = "reports"
        verbose_name = "Food cost"
        verbose_name_plural = "Food cost"


class StaffReport(Order):
    class Meta:
        proxy = True
        app_label = "reports"
        verbose_name = "Staff"
        verbose_name_plural = "Staff"


class ShiftsReport(CashShift):
    class Meta:
        proxy = True
        app_label = "reports"
        verbose_name = "Cash shifts"
        verbose_name_plural = "Cash shifts"


class ReservationsReport(Reservation):
    class Meta:
        proxy = True
        app_label = "reports"
        verbose_name = "Reservations"
        verbose_name_plural = "Reservations"


class ReviewsReport(Review):
    class Meta:
        proxy = True
        app_label = "reports"
        verbose_name = "Reviews"
        verbose_name_plural = "Reviews"


class HoursReport(TimeEntry):
    class Meta:
        proxy = True
        app_label = "reports"
        verbose_name = "Hours"
        verbose_name_plural = "Hours"
