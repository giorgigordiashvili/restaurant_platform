"""
Restaurant filters for API queries.
"""

import django_filters

from .models import Restaurant


class RestaurantFilter(django_filters.FilterSet):
    """Filter restaurants by various criteria."""

    name = django_filters.CharFilter(lookup_expr="icontains")
    city = django_filters.CharFilter(lookup_expr="iexact")
    country = django_filters.CharFilter(lookup_expr="iexact")
    accepts_remote_orders = django_filters.BooleanFilter()
    accepts_reservations = django_filters.BooleanFilter()
    accepts_takeaway = django_filters.BooleanFilter()
    min_rating = django_filters.NumberFilter(field_name="average_rating", lookup_expr="gte")

    class Meta:
        model = Restaurant
        fields = [
            "name",
            "city",
            "country",
            "accepts_remote_orders",
            "accepts_reservations",
            "accepts_takeaway",
            "min_rating",
        ]
