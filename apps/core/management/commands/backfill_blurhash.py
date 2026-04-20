"""
One-time / idempotent backfill of BlurHash strings for existing images.

Run after the migration lands in prod:

    python manage.py backfill_blurhash

Only touches rows where the hash is still empty, so running it twice is
safe and cheap.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.blurhash_utils import generate_blurhash


TARGETS = [
    # (app_label, model_name, image_field, blurhash_field)
    ("menu", "MenuCategory", "image", "image_blurhash"),
    ("menu", "MenuItem", "image", "image_blurhash"),
    ("tenants", "RestaurantCategory", "image", "image_blurhash"),
    ("tenants", "Restaurant", "logo", "logo_blurhash"),
    ("tenants", "Restaurant", "cover_image", "cover_image_blurhash"),
]


class Command(BaseCommand):
    help = "Generate BlurHash strings for existing images that don't have one yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenerate even for rows that already have a blurhash.",
        )
        parser.add_argument(
            "--target",
            help="Limit to one model (e.g. 'menu.MenuItem').",
        )
        parser.add_argument(
            "--async",
            dest="run_async",
            action="store_true",
            help="Enqueue on Celery and return immediately — use this on DO "
            "when the exec session keeps timing out mid-backfill.",
        )

    def handle(self, *args, **opts):
        if opts.get("run_async"):
            from apps.core.tasks import backfill_blurhash_task

            result = backfill_blurhash_task.delay(
                force=opts["force"],
                target=opts.get("target"),
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Enqueued on Celery as task {result.id}. "
                    "Watch 'celery worker' logs (or flower) for progress."
                )
            )
            return

        from django.apps import apps

        force = opts["force"]
        target_filter = opts.get("target")

        for app_label, model_name, image_field, blurhash_field in TARGETS:
            label = f"{app_label}.{model_name}"
            if target_filter and target_filter.lower() != label.lower():
                continue
            model = apps.get_model(app_label, model_name)
            qs = model.objects.exclude(**{image_field: ""}).exclude(**{f"{image_field}__isnull": True})
            if not force:
                qs = qs.filter(**{blurhash_field: ""})
            total = qs.count()
            self.stdout.write(f"[{label}] {total} rows to process")

            done = 0
            for obj in qs.iterator(chunk_size=100):
                image = getattr(obj, image_field)
                if not image:
                    continue
                try:
                    new_hash = generate_blurhash(image)
                except Exception as exc:
                    self.stderr.write(f"  {obj.pk}: {exc}")
                    continue
                if not new_hash:
                    continue
                # Use .update() so the pre_save signal doesn't re-encode.
                model.objects.filter(pk=obj.pk).update(**{blurhash_field: new_hash})
                done += 1
                if done % 25 == 0:
                    self.stdout.write(f"  [{label}] {done}/{total}")
            self.stdout.write(self.style.SUCCESS(f"[{label}] done — {done} rows updated"))
