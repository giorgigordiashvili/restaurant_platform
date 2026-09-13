from decimal import Decimal

import pytest

from apps.ordering import geo, services
from apps.ordering.models import DeliveryZone
from tests.ordering.conftest import FAR, NEAR, TBILISI


def test_distance_and_polygon():
    assert 0.5 < geo.distance_km(*TBILISI, *NEAR) < 0.8
    assert geo.distance_km(*TBILISI, *FAR) > 10
    square = [[41.70, 44.80], [41.70, 44.90], [41.80, 44.90], [41.80, 44.80]]
    assert geo.point_in_polygon(41.75, 44.85, square)
    assert not geo.point_in_polygon(41.85, 44.85, square)
    assert not geo.point_in_polygon(41.75, 44.85, square[:2])
    assert geo.valid_coords(41.7, 44.8) and not geo.valid_coords(0, 0) and not geo.valid_coords("x", 1)


@pytest.mark.django_db
class TestZones:
    def test_ring_then_polygon_precedence(self, ordering, zone):
        outer = DeliveryZone.objects.create(
            restaurant=ordering,
            name="Outer",
            kind="polygon",
            polygon=[[41.60, 44.70], [41.60, 45.00], [41.90, 45.00], [41.90, 44.70]],
            fee=Decimal("9.00"),
            eta_minutes=60,
            sort=1,
        )
        assert services.zone_for(ordering, *NEAR) == zone
        assert services.zone_for(ordering, *FAR) == outer
        assert services.zone_for(ordering, 42.5, 41.6) is None  # Kutaisi
        outer.is_active = False
        outer.save()
        assert services.zone_for(ordering, *FAR) is None

    def test_quote_fee_min_and_free_over(self, ordering, zone):
        q = services.quote_delivery(ordering, *NEAR, Decimal("20"))
        assert q.fee == Decimal("5.00") and q.zone == zone and q.eta_minutes == 40 and q.min_order == Decimal("15.00")
        cfg = services.settings_for(ordering)
        cfg.free_delivery_over = Decimal("50")
        cfg.min_order_delivery = Decimal("25")
        cfg.save()
        q = services.quote_delivery(ordering, *NEAR, Decimal("60"))
        assert q.fee == Decimal("0.00") and q.min_order == Decimal("25.00")
        with pytest.raises(services.FulfilmentError) as exc:
            services.quote_delivery(ordering, *FAR, Decimal("20"))
        assert exc.value.code == "out_of_zone"
        with pytest.raises(services.FulfilmentError) as exc:
            services.quote_delivery(ordering, None, None, Decimal("20"))
        assert exc.value.code == "address_required"

    def test_radius_needs_restaurant_pin(self, ordering, zone):
        ordering.latitude = None
        ordering.save(update_fields=["latitude"])
        zone.refresh_from_db()
        assert not zone.contains(*NEAR)
