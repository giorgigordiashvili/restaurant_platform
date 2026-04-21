"""
Platform admin for reviews — available only in the main Django admin site.

Tenant admin (restaurant owners) gets a separate, read-only list with a
Report action registered in apps/core/tenant_admin.py.
"""

from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

from .models import Review, ReviewMedia, ReviewReport


class ReviewMediaInline(admin.TabularInline):
    model = ReviewMedia
    extra = 0
    readonly_fields = ("kind", "file", "blurhash", "duration_s", "position", "created_at")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = (
        "rating",
        "short_title",
        "restaurant",
        "user",
        "is_hidden",
        "open_reports",
        "created_at",
    )
    list_filter = ("is_hidden", "rating", "restaurant")
    search_fields = ("title", "body", "user__email", "restaurant__name")
    readonly_fields = (
        "order",
        "restaurant",
        "user",
        "created_at",
        "updated_at",
        "edited_at",
    )
    fields = (
        "order",
        "restaurant",
        "user",
        "rating",
        "title",
        "body",
        "is_hidden",
        "edited_at",
        "created_at",
        "updated_at",
    )
    inlines = [ReviewMediaInline]
    actions = ("hide_reviews", "unhide_reviews")

    def short_title(self, obj):
        return obj.title[:40] or obj.body[:40]

    short_title.short_description = "Title"

    def open_reports(self, obj):
        return obj.reports.filter(resolution=ReviewReport.RESOLUTION_NONE).count()

    open_reports.short_description = "Open reports"

    @admin.action(description="Hide selected reviews")
    def hide_reviews(self, request, queryset):
        updated = queryset.update(is_hidden=True)
        self.message_user(request, f"Hid {updated} reviews.", level=messages.SUCCESS)

    @admin.action(description="Unhide selected reviews")
    def unhide_reviews(self, request, queryset):
        updated = queryset.update(is_hidden=False)
        self.message_user(request, f"Unhid {updated} reviews.", level=messages.SUCCESS)


@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    list_display = (
        "review",
        "reason",
        "reporter",
        "resolution",
        "resolved_at",
        "created_at",
    )
    list_filter = ("resolution", "reason", "created_at")
    search_fields = ("review__title", "review__body", "reporter__email", "notes")
    readonly_fields = (
        "review",
        "reporter",
        "reason",
        "notes",
        "resolved_at",
        "resolver",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    actions = ("resolve_kept", "resolve_removed")

    @admin.action(description="Resolve — keep the review")
    def resolve_kept(self, request, queryset):
        n = 0
        for report in queryset.filter(resolution=ReviewReport.RESOLUTION_NONE):
            report.resolution = ReviewReport.RESOLUTION_KEPT
            report.resolved_at = timezone.now()
            report.resolver = request.user
            report.save(update_fields=["resolution", "resolved_at", "resolver", "updated_at"])
            n += 1
        self.message_user(request, f"Resolved {n} report(s) as kept.", level=messages.SUCCESS)

    @admin.action(description="Resolve — remove the review")
    def resolve_removed(self, request, queryset):
        n = 0
        with transaction.atomic():
            for report in queryset.filter(resolution=ReviewReport.RESOLUTION_NONE).select_related("review"):
                report.resolution = ReviewReport.RESOLUTION_REMOVED
                report.resolved_at = timezone.now()
                report.resolver = request.user
                report.save(update_fields=["resolution", "resolved_at", "resolver", "updated_at"])
                report.review.is_hidden = True
                report.review.save(update_fields=["is_hidden", "updated_at"])
                n += 1
        self.message_user(
            request,
            f"Removed {n} review(s) and marked their reports resolved.",
            level=messages.SUCCESS,
        )
