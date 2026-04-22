"""
API views for the reviews app.
"""

from datetime import timedelta

from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiTypes, extend_schema

from apps.core.blurhash_utils import generate_blurhash
from apps.orders.models import Order
from apps.tenants.models import Restaurant

from .models import (
    EDIT_WINDOW_DAYS,
    MAX_IMAGES_PER_REVIEW,
    MAX_VIDEOS_PER_REVIEW,
    Review,
    ReviewMedia,
    ReviewReport,
)
from .permissions import IsReviewOwner, IsStaffOfReviewedRestaurant
from .serializers import (
    EligibleOrderSerializer,
    ReviewCreateSerializer,
    ReviewMediaSerializer,
    ReviewMediaUploadSerializer,
    ReviewReportCreateSerializer,
    ReviewSerializer,
    ReviewStatsSerializer,
    ReviewUpdateSerializer,
)


class ReviewPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 50


def _public_review_qs():
    return (
        Review.objects.filter(is_hidden=False).select_related("user", "restaurant", "order").prefetch_related("media")
    )


# ── Public ────────────────────────────────────────────────────────────────────


@extend_schema(tags=["Reviews"])
class RestaurantReviewsListView(generics.ListAPIView):
    """Public paginated review list for a restaurant."""

    serializer_class = ReviewSerializer
    permission_classes = [AllowAny]
    pagination_class = ReviewPagination

    def get_queryset(self):
        slug = self.kwargs["slug"]
        return _public_review_qs().filter(restaurant__slug=slug)


@extend_schema(tags=["Reviews"], responses=ReviewStatsSerializer)
class RestaurantReviewStatsView(APIView):
    """Average, total, and 1-5 star distribution — no auth required."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
        qs = Review.objects.filter(restaurant=restaurant, is_hidden=False)
        rows = qs.values("rating").annotate(n=Count("id"))
        distribution = {str(i): 0 for i in range(1, 6)}
        for row in rows:
            distribution[str(row["rating"])] = row["n"]
        total = sum(distribution.values())
        average = sum(int(k) * v for k, v in distribution.items()) / total if total else 0.0
        return Response(
            {
                "average": round(average, 2),
                "total": total,
                "distribution": distribution,
            }
        )


# ── Authenticated surfaces ────────────────────────────────────────────────────


@extend_schema(tags=["Reviews"])
class MyReviewsListView(generics.ListAPIView):
    """All reviews written by the current user."""

    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ReviewPagination

    def get_queryset(self):
        return (
            Review.objects.filter(user=self.request.user)
            .select_related("user", "restaurant", "order")
            .prefetch_related("media")
        )


@extend_schema(tags=["Reviews"])
class EligibleOrdersListView(generics.ListAPIView):
    """Completed orders owned by the current user that don't have a review yet."""

    serializer_class = EligibleOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            Order.objects.filter(
                customer=self.request.user,
                status__in=("completed", "served"),
                review__isnull=True,  # via Review.order OneToOne related_name="review"
            )
            .select_related("restaurant")
            .order_by("-completed_at", "-created_at")
        )


@extend_schema(tags=["Reviews"])
class ReviewCreateView(generics.CreateAPIView):
    """Create a new review for a completed order."""

    serializer_class = ReviewCreateSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "review_create"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = serializer.save()
        # Return the read-serializer shape so the frontend gets media=[],
        # user_name, etc. in a single round trip.
        out = ReviewSerializer(review, context=self.get_serializer_context()).data
        return Response(out, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Reviews"])
class ReviewDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve (public), patch (owner, within window), delete (owner anytime)."""

    serializer_class = ReviewSerializer
    lookup_field = "id"
    # Retrieves are public and pass through the default anon/user rates;
    # scoped rate only bites on PATCH / DELETE (see get_throttles below).
    throttle_scope = "review_edit"

    def get_throttles(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        return Review.objects.select_related("user", "restaurant", "order").prefetch_related("media")

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [IsAuthenticated(), IsReviewOwner()]
        return [AllowAny()]

    def partial_update(self, request, *args, **kwargs):
        review = self.get_object()
        if timezone.now() > review.created_at + timedelta(days=EDIT_WINDOW_DAYS):
            raise PermissionDenied("Edit window closed. Delete the review and post a new one.")
        update_ser = ReviewUpdateSerializer(review, data=request.data, partial=True)
        update_ser.is_valid(raise_exception=True)
        update_ser.save(edited_at=timezone.now())
        review.refresh_from_db()
        out = ReviewSerializer(review, context=self.get_serializer_context()).data
        return Response(out)

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)


# ── Media ─────────────────────────────────────────────────────────────────────


@extend_schema(
    tags=["Reviews"],
    request={"multipart/form-data": ReviewMediaUploadSerializer},
    responses=ReviewMediaSerializer,
)
class ReviewMediaUploadView(APIView):
    """Attach an image or video to an existing review. Owner only."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "review_media"

    def post(self, request, review_id):
        review = get_object_or_404(Review, id=review_id)
        if review.user_id != request.user.id:
            raise PermissionDenied("Not your review.")

        serializer = ReviewMediaUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        kind = serializer.validated_data["kind"]
        f = serializer.validated_data["file"]
        duration_s = serializer.validated_data.get("duration_s")

        existing_images = review.media.filter(kind=ReviewMedia.IMAGE).count()
        existing_videos = review.media.filter(kind=ReviewMedia.VIDEO).count()
        if kind == ReviewMedia.IMAGE and existing_images >= MAX_IMAGES_PER_REVIEW:
            return Response(
                {"detail": f"Maximum {MAX_IMAGES_PER_REVIEW} images per review."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if kind == ReviewMedia.VIDEO and existing_videos >= MAX_VIDEOS_PER_REVIEW:
            return Response(
                {"detail": f"Maximum {MAX_VIDEOS_PER_REVIEW} video per review."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        blurhash = ""
        if kind == ReviewMedia.IMAGE:
            blurhash = generate_blurhash(f)
            # generate_blurhash seeks(0), but FileField stores from the
            # current position — rewind explicitly before handing it off.
            try:
                f.seek(0)
            except Exception:
                pass

        position = review.media.count()
        media = ReviewMedia.objects.create(
            review=review,
            kind=kind,
            file=f,
            blurhash=blurhash,
            duration_s=duration_s if kind == ReviewMedia.VIDEO else None,
            position=position,
        )
        return Response(
            ReviewMediaSerializer(media, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Reviews"])
class ReviewMediaDeleteView(APIView):
    """Remove a media item from a review. Owner only."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, review_id, media_id):
        review = get_object_or_404(Review, id=review_id)
        if review.user_id != request.user.id:
            raise PermissionDenied("Not your review.")
        media = get_object_or_404(ReviewMedia, id=media_id, review=review)
        media.file.delete(save=False)
        media.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Reporting ─────────────────────────────────────────────────────────────────


@extend_schema(tags=["Reviews"], request=ReviewReportCreateSerializer, responses=OpenApiTypes.OBJECT)
class ReviewReportCreateView(APIView):
    """Flag a review for platform-admin moderation. Restaurant staff only."""

    permission_classes = [IsAuthenticated, IsStaffOfReviewedRestaurant]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "review_report"

    def post(self, request, review_id):
        review = get_object_or_404(Review, id=review_id)
        # has_object_permission doesn't fire automatically for APIView.post
        # — do the check explicitly against the review object we just loaded.
        self.check_object_permissions(request, review)

        serializer = ReviewReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        report, created = ReviewReport.objects.get_or_create(
            review=review,
            reporter=request.user,
            defaults={
                "reason": serializer.validated_data["reason"],
                "notes": serializer.validated_data.get("notes", ""),
            },
        )
        if not created:
            return Response(
                {"detail": "You've already reported this review."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        open_count = review.reports.filter(resolution=ReviewReport.RESOLUTION_NONE).count()
        return Response(
            {"id": str(report.id), "open_reports": open_count},
            status=status.HTTP_201_CREATED,
        )
