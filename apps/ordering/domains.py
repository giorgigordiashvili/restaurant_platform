"""Custom domains: a restaurant points ``order.example.ge`` at us (CNAME to the sites host or A record to our IP)."""

from __future__ import annotations

import socket

from django.conf import settings
from django.utils import timezone

from apps.ordering.models import RestaurantDomain


def expected_targets() -> dict:
    return {
        "cname": getattr(settings, "SITES_CNAME_TARGET", "sites.aimenu.ge"),
        "ip": getattr(settings, "PUBLIC_IP", ""),
    }


def resolves_to_us(domain: str) -> tuple[bool, str]:
    targets = expected_targets()
    ips = set()
    try:
        for info in socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP):
            ips.add(info[4][0])
    except (socket.gaierror, UnicodeError, OSError) as exc:
        return False, f"DNS lookup failed: {exc}"
    ours = set()
    if targets["ip"]:
        ours.add(targets["ip"])
    if targets["cname"]:
        try:
            for info in socket.getaddrinfo(targets["cname"], 443, proto=socket.IPPROTO_TCP):
                ours.add(info[4][0])
        except (socket.gaierror, OSError):
            pass
    if not ours:
        return False, "Server address is not configured (PUBLIC_IP)."
    if ips & ours:
        return True, ""
    return False, f"{domain} points to {', '.join(sorted(ips))}, expected {', '.join(sorted(ours))}."


def verify(row: RestaurantDomain) -> bool:
    ok, error = resolves_to_us(row.domain)
    row.last_check_at = timezone.now()
    row.error = error[:200]
    if ok:
        row.verified_at = row.verified_at or timezone.now()
    else:
        row.verified_at = None
    row.save(update_fields=["last_check_at", "error", "verified_at", "updated_at"])
    return ok


def lookup(host: str) -> RestaurantDomain | None:
    host = (host or "").split(":")[0].strip().lower().rstrip(".")
    if not host:
        return None
    return RestaurantDomain.objects.select_related("restaurant").filter(domain=host, restaurant__is_active=True).first()


def is_known(host: str) -> bool:
    """Caddy on-demand TLS "ask": only mint certificates for domains a restaurant registered."""
    return lookup(host) is not None
