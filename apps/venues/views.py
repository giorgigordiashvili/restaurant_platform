"""Public (customer-facing) venue endpoints."""

import logging

from django.db.models import Prefetch

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.menu.serializers import FullMenuSerializer
from apps.tenants.models import Restaurant

from . import services
from .models import Venue, VenueMember, VenueTable
from .serializers import (
    VenueCardSerializer,
    VenueDetailResponseSerializer,
    VenueMenuResponseSerializer,
    VenueRestaurantCardSerializer,
    VenueTablePublicSerializer,
    VenueValidateResponseSerializer,
)

logger = logging.getLogger(__name__)

PUBLIC_CACHE = "public, max-age=60, stale-while-revalidate=300"


def _not_found(message="Venue not found."):
    return Response({"success": False, "error": {"message": message}}, status=status.HTTP_404_NOT_FOUND)


def _members(venue, only_ordering=False):
    qs = (
        Restaurant.objects.filter(is_active=True, venue_membership__venue=venue)
        .select_related("category", "venue_membership")
        .prefetch_related("operating_hours")
        .order_by("venue_membership__display_order", "name")
    )
    if only_ordering:
        qs = qs.filter(accepts_remote_orders=True)
    return qs


def _active_venue(slug):
    return Venue.objects.filter(slug=slug, is_active=True).first()


@extend_schema(tags=["Venues"], responses={200: VenueDetailResponseSerializer, 404: None})
class VenueDetailView(APIView):
    """Venue card plus its member restaurants (table-agnostic)."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        venue = _active_venue(slug)
        if venue is None:
            return _not_found()
        data = {
            "venue": VenueCardSerializer(venue, context={"request": request}).data,
            "restaurants": VenueRestaurantCardSerializer(_members(venue), many=True, context={"request": request}).data,
        }
        response = Response({"success": True, "data": data})
        response["Cache-Control"] = PUBLIC_CACHE
        return response


@extend_schema(tags=["Venues"], responses={200: VenueValidateResponseSerializer, 404: None, 410: None})
class VenueTableValidateView(APIView):
    """
    Resolve a venue QR code: the table, and every member restaurant with that
    restaurant's own table code, so the client can continue with the unchanged
    per-restaurant validate → session → order flow. Creates no session.
    """

    permission_classes = [AllowAny]

    def get(self, request, code):
        venue_table = VenueTable.objects.select_related("venue", "section").filter(code=code, is_active=True).first()
        if venue_table is None:
            return _not_found("Invalid or inactive table code.")
        if not venue_table.venue.is_active:
            return Response(
                {"success": False, "error": {"code": "venue_dissolved", "message": "This venue is no longer active."}},
                status=status.HTTP_410_GONE,
            )
        venue_table.record_scan()

        mirrors = services.member_tables_for(venue_table)
        restaurants = []
        for restaurant in _members(venue_table.venue):
            table = mirrors.get(restaurant.pk)
            if table is None:
                logger.warning("venue table %s has no mirror at %s", venue_table.pk, restaurant.slug)
            restaurants.append(
                {
                    "restaurant": VenueRestaurantCardSerializer(restaurant, context={"request": request}).data,
                    "table_id": str(table.pk) if table else None,
                    "table_code": services.table_code(table),
                }
            )
        data = {
            "venue": VenueCardSerializer(venue_table.venue, context={"request": request}).data,
            "table": VenueTablePublicSerializer(venue_table).data,
            "restaurants": restaurants,
        }
        response = Response({"success": True, "data": data})
        response["Cache-Control"] = "no-store"
        return response


@extend_schema(
    tags=["Venues"],
    parameters=[
        OpenApiParameter(
            "restaurants", str, required=False, description="Comma-separated member slugs to include (default: all)"
        )
    ],
    responses={200: VenueMenuResponseSerializer, 404: None},
)
class VenueMenuView(APIView):
    """Combined menu: one full menu per member restaurant that accepts orders."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        venue = _active_venue(slug)
        if venue is None:
            return _not_found()
        members = _members(venue, only_ordering=True)
        wanted = [s for s in (request.query_params.get("restaurants") or "").split(",") if s]
        if wanted:
            members = members.filter(slug__in=wanted)
        entries = [
            {
                "restaurant": VenueRestaurantCardSerializer(r, context={"request": request}).data,
                "menu": FullMenuSerializer(r, instance=r, context={"request": request}).data,
            }
            for r in members
        ]
        response = Response(
            {
                "success": True,
                "data": {
                    "venue": VenueCardSerializer(venue, context={"request": request}).data,
                    "restaurants": entries,
                },
            }
        )
        response["Cache-Control"] = PUBLIC_CACHE
        return response
