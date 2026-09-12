"""
Backfill the money ledger from what existed before it:

* every Payment gets its ``restaurant`` and an allocation to its order;
* BOG-mirrored payments become ``online_bog`` (session settles allocate
  across the covered orders);
* completed ``CASH-`` settle transactions and approved Flitt transactions
  that never had a Payment row get one.

Idempotent: re-running creates nothing twice (keyed on external_payment_id
and on existing allocations).
"""

from decimal import Decimal

from django.db import migrations
from django.db.models import Sum

ZERO = Decimal("0")


def _allocate(PaymentAllocation, payment, orders):
    """Greedy oldest-first allocation of payment.amount across ``orders`` (skips when allocations exist)."""
    if PaymentAllocation.objects.filter(payment=payment).exists():
        return
    remaining = Decimal(payment.amount or 0)
    orders = sorted(orders, key=lambda o: o.created_at)
    rows = []
    for i, o in enumerate(orders):
        if remaining <= ZERO:
            break
        share = min(Decimal(o.total or 0), remaining) if i < len(orders) - 1 else remaining
        if share > ZERO:
            rows.append(PaymentAllocation(payment=payment, order=o, amount=share))
            remaining -= share
    if remaining > ZERO and rows:
        rows[-1].amount += remaining
    PaymentAllocation.objects.bulk_create(rows)


def backfill(apps, schema_editor):
    Payment = apps.get_model("payments", "Payment")
    PaymentAllocation = apps.get_model("payments", "PaymentAllocation")
    Refund = apps.get_model("payments", "Refund")
    BogTransaction = apps.get_model("payments", "BogTransaction")
    FlittTransaction = apps.get_model("payments", "FlittTransaction")

    # 1. restaurant on every payment / refund
    for p in Payment.objects.filter(restaurant__isnull=True, order__isnull=False).select_related("order").iterator(chunk_size=200):
        Payment.objects.filter(pk=p.pk).update(restaurant_id=p.order.restaurant_id)
    for r in Refund.objects.filter(restaurant__isnull=True).select_related("payment").iterator(chunk_size=200):
        Refund.objects.filter(pk=r.pk).update(restaurant_id=r.payment.restaurant_id, order_id=r.payment.order_id)

    # 2. BOG-mirrored payments: method + allocations
    bog_by_id = {t.bog_order_id: t for t in BogTransaction.objects.exclude(bog_order_id="").iterator(chunk_size=200)}
    for p in Payment.objects.exclude(external_payment_id="").select_related("order").iterator(chunk_size=200):
        txn = bog_by_id.get(p.external_payment_id)
        if txn is None:
            continue
        updates = {}
        if p.payment_method in ("card", "mobile"):
            updates["payment_method"] = "online_bog"
        if txn.session_id and not p.session_id:
            updates["session_id"] = txn.session_id
        if updates:
            Payment.objects.filter(pk=p.pk).update(**updates)
        covered = list(txn.covered_orders.all()) if txn.flow_type == "session_settle" else []
        _allocate(PaymentAllocation, p, covered or ([p.order] if p.order_id else []))

    # 3. every other payment with an order: allocate to it
    for p in Payment.objects.filter(order__isnull=False).select_related("order").iterator(chunk_size=200):
        if not PaymentAllocation.objects.filter(payment=p).exists():
            _allocate(PaymentAllocation, p, [p.order])

    # 4. CASH- settle transactions (staff "paid in cash") -> cash payments
    for txn in (
        BogTransaction.objects.filter(flow_type="cash_settle", status="completed")
        .prefetch_related("covered_orders")
        .select_related("session")
        .iterator(chunk_size=200)
    ):
        if Payment.objects.filter(external_payment_id=txn.bog_order_id).exists():
            continue
        orders = list(txn.covered_orders.all())
        if not orders:
            continue
        first = sorted(orders, key=lambda o: o.created_at)[0]
        p = Payment.objects.create(
            restaurant_id=first.restaurant_id,
            order=first,
            session_id=txn.session_id,
            customer_id=first.customer_id,
            processed_by_id=txn.initiated_by_id,
            amount=txn.amount,
            tip_amount=ZERO,
            total_amount=txn.amount,
            payment_method="cash",
            status="completed",
            currency=txn.currency or "GEL",
            external_payment_id=txn.bog_order_id,
            receipt_number="",
            completed_at=txn.created_at,
            notes="Backfilled from cash settle",
        )
        _allocate(PaymentAllocation, p, orders)

    # 5. approved Flitt order payments without a Payment row
    for txn in FlittTransaction.objects.filter(status="approved", order__isnull=False).select_related("order").iterator(chunk_size=200):
        ext = f"flitt:{txn.flitt_order_id}"
        if Payment.objects.filter(external_payment_id=ext).exists():
            continue
        p = Payment.objects.create(
            restaurant_id=txn.order.restaurant_id,
            order=txn.order,
            session_id=txn.session_id,
            customer_id=txn.order.customer_id,
            amount=txn.amount,
            tip_amount=ZERO,
            total_amount=txn.amount,
            payment_method="online_flitt",
            status="completed",
            currency=txn.currency or "GEL",
            external_payment_id=ext,
            receipt_number="",
            completed_at=txn.last_webhook_at or txn.created_at,
            notes="Backfilled from Flitt transaction",
        )
        _allocate(PaymentAllocation, p, [txn.order])


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0006_cash_shifts_ledger"),
        ("orders", "0008_discounts_voids"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
