"""
Pre-save signal wiring that regenerates the BlurHash string whenever an
image field on a model changes.

Each target model registers via ``register_blurhash(Model, image_field,
blurhash_field)``; the app's ``ready()`` calls that once per image field.
Centralising here avoids duplicating the "has it changed?" check across
apps.
"""

from __future__ import annotations

import logging
from typing import Iterable

from django.db.models.signals import pre_save

from .blurhash_utils import generate_blurhash

logger = logging.getLogger(__name__)


def _has_image_changed(instance, field_name: str) -> bool:
    """
    True if the image file attached to ``instance.<field_name>`` differs
    from the one already in the database. Also True for fresh inserts that
    arrive with an image attached.
    """
    if instance.pk is None:
        return bool(getattr(instance, field_name, None))
    try:
        old = instance.__class__.objects.only(field_name).get(pk=instance.pk)
    except instance.__class__.DoesNotExist:
        return True
    old_image = getattr(old, field_name)
    new_image = getattr(instance, field_name)
    old_name = old_image.name if old_image else ""
    new_name = new_image.name if new_image else ""
    return old_name != new_name


def register_blurhash(model, *, image_field: str, blurhash_field: str):
    """Wire a pre_save handler that keeps ``blurhash_field`` in sync with
    ``image_field`` on the given model. Safe to call multiple times — the
    dispatch_uid makes it idempotent."""

    def handler(sender, instance, **kwargs):
        image = getattr(instance, image_field, None)
        if not image:
            # Image cleared → clear hash.
            setattr(instance, blurhash_field, "")
            return
        if not _has_image_changed(instance, image_field):
            # Image unchanged on update — leave the existing hash alone.
            return
        try:
            new_hash = generate_blurhash(image)
        except Exception:
            logger.warning(
                "blurhash: unexpected failure on %s.%s", sender.__name__, image_field,
                exc_info=True,
            )
            new_hash = ""
        setattr(instance, blurhash_field, new_hash)

    pre_save.connect(
        handler,
        sender=model,
        weak=False,
        dispatch_uid=f"blurhash:{model._meta.label}:{image_field}",
    )


def register_many(model, pairs: Iterable[tuple[str, str]]):
    """Convenience — register multiple (image_field, blurhash_field) pairs
    on the same model."""
    for image_field, blurhash_field in pairs:
        register_blurhash(model, image_field=image_field, blurhash_field=blurhash_field)
