"""
Signals that keep Restaurant.average_rating + total_reviews in sync with
the reviews table. We listen for post_save / post_delete on Review; the
aggregate is recomputed in a transaction.on_commit callback so the
Review row is actually persisted before we read it.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.tenants.models import Restaurant

from .models import Review


def _recompute_for_restaurant(restaurant_id):
    agg = Review.objects.filter(restaurant_id=restaurant_id, is_hidden=False).aggregate(
        avg=Avg("rating"), n=Count("id")
    )
    avg = agg["avg"] or Decimal("0")
    n = agg["n"] or 0
    Restaurant.objects.filter(pk=restaurant_id).update(
        average_rating=round(Decimal(avg), 2),
        total_reviews=n,
    )


@receiver(post_save, sender=Review)
def review_saved(sender, instance, created, **kwargs):
    # created + is_hidden changes both affect the aggregate; easiest is to
    # recompute on every save. The query is indexed on (restaurant, is_hidden).
    restaurant_id = instance.restaurant_id
    transaction.on_commit(lambda: _recompute_for_restaurant(restaurant_id))


@receiver(post_delete, sender=Review)
def review_deleted(sender, instance, **kwargs):
    restaurant_id = instance.restaurant_id
    transaction.on_commit(lambda: _recompute_for_restaurant(restaurant_id))
