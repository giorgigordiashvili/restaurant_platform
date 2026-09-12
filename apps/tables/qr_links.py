"""
Dynamic QR short links.

A printed code encodes https://aimenu.ge/q/<code>; the destination is decided
here at scan time from the code's *current* state, so a table can be
reassigned, join or leave a shared venue, or be pointed at a promo without
reprinting anything. Both TableQRCode codes and VenueTable codes resolve here.
"""

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

DESTINATION_AUTO = "auto"
DESTINATION_RESTAURANT = "restaurant"
DESTINATION_VENUE = "venue"
DESTINATION_MENU_ONLY = "menu_only"
DESTINATION_CUSTOM = "custom"
DESTINATION_CHOICES = [
    (DESTINATION_AUTO, "Automatic — follows the table (restaurant, or the shared venue)"),
    (DESTINATION_RESTAURANT, "Always this restaurant's page"),
    (DESTINATION_VENUE, "Shared venue page (falls back to the restaurant)"),
    (DESTINATION_MENU_ONLY, "Menu only — browse, no ordering session"),
    (DESTINATION_CUSTOM, "Custom URL"),
]

# Both code namespaces (token_urlsafe and "v_" + token_urlsafe) fit this.
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_http_url = URLValidator(schemes=["http", "https"])


def frontend_base():
    return settings.FRONTEND_BASE_URL.rstrip("/")


def short_link(code):
    return f"{frontend_base()}/q/{code}"


def validate_custom_url(value):
    """Managers may point a code anywhere on the web -- except back at a short link."""
    value = (value or "").strip()
    if not value:
        raise ValidationError("A custom destination needs a URL.")
    if len(value) > 2000:
        raise ValidationError("URL is too long.")
    _http_url(value)
    parts = urlsplit(value)
    if "@" in parts.netloc:
        raise ValidationError("Credentials in the URL are not allowed.")
    if parts.path.startswith("/q/") and parts.netloc == urlsplit(frontend_base()).netloc:
        raise ValidationError("A QR code cannot point at another QR short link.")
    return value


@dataclass(frozen=True)
class QRDestination:
    kind: str  # restaurant | venue | menu | custom
    path: str | None  # platform-relative path; None for custom
    url: str  # absolute
    restaurant_slug: str | None = None
    venue_slug: str | None = None
    table_code: str | None = None

    def as_dict(self):
        return asdict(self)


def _platform(kind, path, **extra):
    return QRDestination(kind=kind, path=path, url=f"{frontend_base()}{path}", **extra)


def _for_table_qr(qr):
    from apps.venues.services import venue_for_table

    table = qr.table
    slug = table.restaurant.slug
    venue, venue_table = venue_for_table(table)
    destination = qr.destination

    if destination == DESTINATION_CUSTOM and qr.custom_url:
        return QRDestination(kind="custom", path=None, url=qr.custom_url, restaurant_slug=slug)
    if destination == DESTINATION_MENU_ONLY:
        return _platform("menu", f"/restaurant/{slug}", restaurant_slug=slug)
    if destination == DESTINATION_RESTAURANT:
        query = {"table": qr.code}
        if venue is not None:
            query["via"] = "venue"  # tells the frontend not to bounce to the venue page
        return _platform(
            "restaurant", f"/restaurant/{slug}?{urlencode(query)}", restaurant_slug=slug, table_code=qr.code
        )
    if venue is not None:  # auto, or an explicit venue preference
        return _platform(
            "venue",
            f"/venue/{venue.slug}?{urlencode({'table': venue_table.code})}",
            restaurant_slug=slug,
            venue_slug=venue.slug,
            table_code=venue_table.code,
        )
    return _platform(
        "restaurant", f"/restaurant/{slug}?{urlencode({'table': qr.code})}", restaurant_slug=slug, table_code=qr.code
    )


def resolve(code, *, record=False):
    """
    The current destination for a code, or None when it is unknown or retired.

    ``record=True`` counts a short-link hit on whichever model owns the code
    (``resolves_count``); ``scans_count`` stays what the validate endpoints
    count for old, direct-URL stickers.
    """
    from apps.tables.models import TableQRCode
    from apps.venues.models import VenueTable

    if not code or not CODE_RE.match(code):
        return None

    qr = (
        TableQRCode.objects.select_related("table__restaurant", "table__venue_table__venue")
        .filter(code=code, is_active=True, table__is_active=True, table__restaurant__is_active=True)
        .first()
    )
    if qr is not None:
        destination = _for_table_qr(qr)
        if record:
            qr.record_resolve()
        return destination

    venue_table = (
        VenueTable.objects.select_related("venue").filter(code=code, is_active=True, venue__is_active=True).first()
    )
    if venue_table is not None:
        if record:
            venue_table.record_resolve()
        return _platform(
            "venue",
            f"/venue/{venue_table.venue.slug}?{urlencode({'table': venue_table.code})}",
            venue_slug=venue_table.venue.slug,
            table_code=venue_table.code,
        )
    return None
