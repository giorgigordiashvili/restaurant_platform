"""
Core views including health checks.
"""

import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.core.middleware.tenant import TenantMiddleware
from apps.tenants.models import Restaurant

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([])  # health is polled every few seconds by the container/monitor — never throttle it
def health_check(request):
    """
    Health check endpoint for load balancers and monitoring.

    Checks:
    - Database connectivity
    - Cache connectivity
    """
    health_status = {
        "status": "healthy",
        "database": "ok",
        "cache": "ok",
    }

    # Check database
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as e:
        health_status["database"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"

    # Check cache
    try:
        cache.set("health_check", "ok", 10)
        if cache.get("health_check") != "ok":
            raise Exception("Cache read/write failed")
    except Exception as e:
        health_status["cache"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"

    status_code = status.HTTP_200_OK if health_status["status"] == "healthy" else status.HTTP_503_SERVICE_UNAVAILABLE

    return Response(health_status, status=status_code)


@api_view(["GET"])
@permission_classes([AllowAny])
def readiness_check(request):
    """
    Readiness check endpoint - simple response to verify app is running.
    """
    return Response({"status": "ready"}, status=status.HTTP_200_OK)


# How long an on-demand TLS verdict stays cached. Caddy only asks once per
# hostname before it has a cert, but a stranger pointing DNS at us can spray
# random SNI and turn every handshake into a DB query, so cache both answers.
TLS_CHECK_CACHE_TTL = 300

# Anything that is not hostname-shaped is rejected before it is used to build a
# cache key, which keeps junk (spaces, control characters, absurd lengths) out
# of the cache backend.
TLS_DOMAIN_RE = re.compile(r"^[a-z0-9.-]{1,253}$")


def _tls_domain_allowed(domain):
    """
    Decide whether we want a publicly-trusted certificate for ``domain``.

    Only two shapes qualify: the tenant-admin domain itself, and
    ``<slug>.<ADMIN_DOMAIN>`` where the slug belongs to an active restaurant.
    """
    admin_domain = getattr(settings, "ADMIN_DOMAIN", "")
    if not admin_domain:
        return False

    if domain == admin_domain:
        return True

    suffix = f".{admin_domain}"
    if not domain.endswith(suffix):
        return False

    slug = domain[: -len(suffix)]

    # Exactly one label: "a.b.admin.aimenu.ge" is never one of ours.
    if not slug or "." in slug:
        return False

    if slug in TenantMiddleware.EXCLUDED_SUBDOMAINS:
        return False

    return Restaurant.objects.filter(slug=slug, is_active=True).exists()


@require_GET
def tls_check(request):
    """
    Caddy on-demand TLS ``ask`` endpoint.

    During the TLS handshake, for any hostname Caddy does not already hold a
    certificate for, it calls:

        GET /api/v1/tls-check/?domain=tiali.admin.aimenu.ge

    2xx means "go ahead and issue"; anything else aborts the handshake. This
    is the only thing between the public internet and unbounded issuance --
    any domain pointed at our IP would otherwise burn Let's Encrypt rate
    limits -- so it stays strict and never falls open on error.
    """
    domain = request.GET.get("domain", "").strip().lower().rstrip(".")

    if not TLS_DOMAIN_RE.match(domain):
        return HttpResponseForbidden("denied")

    cache_key = f"tls_check:{domain}"
    allowed = cache.get(cache_key)

    if allowed is None:
        try:
            allowed = _tls_domain_allowed(domain)
        except Exception:
            # A DB blip must not authorize issuance, and must not be cached.
            logger.exception("tls_check failed for %s", domain)
            return HttpResponseForbidden("denied")
        cache.set(cache_key, allowed, TLS_CHECK_CACHE_TTL)

    if allowed:
        return HttpResponse("ok")
    return HttpResponseForbidden("denied")
