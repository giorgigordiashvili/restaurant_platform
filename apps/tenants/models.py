"""
Restaurant (tenant) models - to be implemented in Phase 2.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class Restaurant(TimeStampedModel):
    """Restaurant model - stub for Phase 1."""
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    owner = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='owned_restaurants'
    )

    class Meta:
        db_table = 'restaurants'

    def __str__(self):
        return self.name
