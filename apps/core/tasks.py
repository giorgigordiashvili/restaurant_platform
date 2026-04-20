"""
Celery tasks for the core app. Anything that (a) takes long enough to break
the DO App Platform ``exec`` sandbox window or (b) should survive request /
shell disconnects belongs here.
"""

from __future__ import annotations

import logging

from celery import shared_task

from .blurhash_utils import generate_blurhash

logger = logging.getLogger(__name__)

_BLURHASH_TARGETS = [
    # (app_label, model_name, image_field, blurhash_field)
    ("menu", "MenuCategory", "image", "image_blurhash"),
    ("menu", "MenuItem", "image", "image_blurhash"),
    ("tenants", "RestaurantCategory", "image", "image_blurhash"),
    ("tenants", "Restaurant", "logo", "logo_blurhash"),
    ("tenants", "Restaurant", "cover_image", "cover_image_blurhash"),
]


@shared_task(name="core.backfill_blurhash", ignore_result=False)
def backfill_blurhash_task(force: bool = False, target: str | None = None) -> dict:
    """
    Walk every image field listed in _BLURHASH_TARGETS and populate the
    paired blurhash column where it's still empty. Commits per row via
    ``.update()`` so progress survives any interruption.

    Returns a ``{model_label: rows_updated}`` summary when it finishes.
    Safe to re-run — rows that already have a hash are skipped unless
    ``force=True``.
    """
    from django.apps import apps

    target_filter = (target or "").lower() or None
    summary: dict[str, int] = {}

    for app_label, model_name, image_field, blurhash_field in _BLURHASH_TARGETS:
        label = f"{app_label}.{model_name}"
        if target_filter and target_filter != label.lower():
            continue
        model = apps.get_model(app_label, model_name)
        qs = model.objects.exclude(**{image_field: ""}).exclude(**{f"{image_field}__isnull": True})
        if not force:
            qs = qs.filter(**{blurhash_field: ""})
        total = qs.count()
        logger.info("blurhash backfill %s: %s rows to process", label, total)

        updated = 0
        for obj in qs.iterator(chunk_size=50):
            image = getattr(obj, image_field, None)
            if not image:
                continue
            try:
                new_hash = generate_blurhash(image)
            except Exception:
                logger.exception("blurhash backfill %s #%s: encode failed", label, obj.pk)
                continue
            if not new_hash:
                continue
            model.objects.filter(pk=obj.pk).update(**{blurhash_field: new_hash})
            updated += 1
        logger.info("blurhash backfill %s: %s rows updated", label, updated)
        summary[label] = updated

    return summary
