"""Module hook: keep the (legacy) ReservationSettings switch in step with the Restaurant flag."""

from apps.reservations.models import ReservationSettings


def on_module_toggled(restaurant, enabled, *, by=None):
    ReservationSettings.objects.filter(restaurant=restaurant).update(accepts_reservations=enabled)
