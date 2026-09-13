"""Small geo helpers: haversine distance and point-in-polygon (no GIS dependency)."""

from __future__ import annotations

import math
from decimal import Decimal

EARTH_KM = 6371.0088


def _f(v) -> float:
    return float(v) if not isinstance(v, float) else v


def distance_km(lat1, lng1, lat2, lng2) -> float:
    la1, lo1, la2, lo2 = (math.radians(_f(v)) for v in (lat1, lng1, lat2, lng2))
    dlat = la2 - la1
    dlng = lo2 - lo1
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def point_in_polygon(lat, lng, polygon) -> bool:
    """Ray casting over ``[[lat, lng], ...]`` (at least three vertices; closed or open ring)."""
    pts = [(_f(p[0]), _f(p[1])) for p in polygon or [] if len(p) >= 2]
    if len(pts) < 3:
        return False
    x, y = _f(lng), _f(lat)
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        yi, xi = pts[i]
        yj, xj = pts[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def valid_coords(lat, lng) -> bool:
    try:
        la, lo = _f(lat), _f(lng)
    except (TypeError, ValueError):
        return False
    return -90 <= la <= 90 and -180 <= lo <= 180 and not (la == 0 and lo == 0)


def money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))
