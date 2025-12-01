"""
Custom storage backends for MinIO/S3.
"""

import os
import uuid

from django.conf import settings

from storages.backends.s3boto3 import S3Boto3Storage


class MediaStorage(S3Boto3Storage):
    """
    Custom storage backend for media files.
    """

    bucket_name = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "media")
    location = "media"
    file_overwrite = False
    default_acl = "public-read"


class PrivateMediaStorage(S3Boto3Storage):
    """
    Storage backend for private media files (e.g., invoices).
    """

    bucket_name = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "media")
    location = "private"
    file_overwrite = False
    default_acl = "private"
    querystring_auth = True
    querystring_expire = 3600  # 1 hour


def get_upload_path(instance, filename, folder="uploads"):
    """
    Generate a unique upload path for files.

    Args:
        instance: The model instance
        filename: Original filename
        folder: Subfolder in media

    Returns:
        Path like: folder/2024/01/uuid-filename.ext
    """
    from django.utils import timezone

    ext = os.path.splitext(filename)[1].lower()
    unique_id = uuid.uuid4().hex[:8]
    now = timezone.now()

    return f"{folder}/{now.year}/{now.month:02d}/{unique_id}{ext}"


def restaurant_logo_path(instance, filename):
    """Upload path for restaurant logos."""
    return get_upload_path(instance, filename, f"restaurants/{instance.slug}/logo")


def restaurant_cover_path(instance, filename):
    """Upload path for restaurant cover images."""
    return get_upload_path(instance, filename, f"restaurants/{instance.slug}/cover")


def menu_category_image_path(instance, filename):
    """Upload path for menu category images."""
    return get_upload_path(instance, filename, f"restaurants/{instance.restaurant.slug}/menu/categories")


def menu_item_image_path(instance, filename):
    """Upload path for menu item images."""
    return get_upload_path(instance, filename, f"restaurants/{instance.restaurant.slug}/menu/items")


def user_avatar_path(instance, filename):
    """Upload path for user avatars."""
    return get_upload_path(instance, filename, f"users/{instance.id}/avatar")


def qr_code_path(instance, filename):
    """Upload path for table QR codes."""
    return get_upload_path(instance, filename, f"restaurants/{instance.table.restaurant.slug}/qrcodes")
