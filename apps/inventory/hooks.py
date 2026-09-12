"""
The only warehouse entry points other apps call.

Every hook is a no-op while ``Restaurant.warehouse_enabled`` is off, so
existing tenants see no change. Status hooks never raise: a stock problem
must not break a payment webhook or a kitchen screen -- the reconciliation
job (apps.inventory.tasks) picks up anything that slipped.
"""

from __future__ import annotations

import logging

from apps.inventory import services

logger = logging.getLogger(__name__)


def on_order_created(order):
    """Reserve ingredients inside the caller's transaction; may raise InsufficientStock."""
    if not services.enabled(order.restaurant):
        return None
    return services.reserve_for_order(order, strict=True)


def on_order_items_added(order, order_items):
    """Dishes added to a live order: reserve them (or consume, if the kitchen already has the order)."""
    if not services.enabled(order.restaurant):
        return None
    return services.reserve_order_items(order, order_items, strict=True)


def on_order_status_changed(order, old_status, new_status, *, by=None):
    if not services.enabled(order.restaurant) or old_status == new_status:
        return
    try:
        if new_status in services.CONSUMING_STATUSES and old_status in services.PRE_KITCHEN_STATUSES:
            services.consume_order(order, by=by)
        elif new_status == "cancelled":
            if not services.release_reservation(order):
                services.restore_order(order, by=by)
        elif new_status == "pending" and old_status == "pending_payment":
            # Paid: make sure the hold still exists (the TTL job may have
            # released an abandoned checkout). Never refuse a paid order.
            services.reserve_for_order(order, strict=False)
    except Exception:
        logger.exception("Warehouse hook failed for order %s (%s -> %s)", order.pk, old_status, new_status)


def on_order_item_cancelled(order_item, *, by=None):
    try:
        services.release_order_item(order_item, by=by)
    except Exception:
        logger.exception("Warehouse hook failed for order item %s", order_item.pk)


def on_recipe_changed(target, *, by=None):
    """A dish or modifier recipe was edited: re-evaluate its availability."""
    restaurant_id = target.restaurant_id if hasattr(target, "restaurant_id") else target.group.restaurant_id
    from apps.tenants.models import Restaurant

    if not Restaurant.objects.filter(pk=restaurant_id, warehouse_enabled=True).exists():
        return
    ids = list(target.recipe_lines.values_list("stock_item_id", flat=True))
    if ids:
        services.recompute_availability(restaurant_id, ids)
    elif target.auto_disabled_by_stock:
        # Recipe removed entirely -> untracked again.
        type(target).objects.filter(pk=target.pk).update(auto_disabled_by_stock=False)
    services._audit("recipe_update", restaurant_id, by, f"Recipe updated for {target}", target)


def on_feature_toggled(restaurant, enabled, *, by=None):
    services.on_feature_toggled(restaurant, enabled, by=by)
