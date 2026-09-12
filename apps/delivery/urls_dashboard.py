from django.urls import path

from .views import (
    MenuImportApplyView,
    MenuImportDetailView,
    MenuImportPreviewView,
    MenuSyncView,
    PauseStoreView,
    PlatformListView,
    ResumeStoreView,
    StoreStatusView,
)

urlpatterns = [
    path("platforms/", PlatformListView.as_view(), name="delivery-platforms"),
    path("platforms/<slug:code>/status/", StoreStatusView.as_view(), name="delivery-platform-status"),
    path("platforms/<slug:code>/pause/", PauseStoreView.as_view(), name="delivery-platform-pause"),
    path("platforms/<slug:code>/resume/", ResumeStoreView.as_view(), name="delivery-platform-resume"),
    path("platforms/<slug:code>/menu-sync/", MenuSyncView.as_view(), name="delivery-platform-menu-sync"),
    path("platforms/<slug:code>/import-menu/", MenuImportPreviewView.as_view(), name="delivery-import-preview"),
    path("imports/<uuid:import_id>/", MenuImportDetailView.as_view(), name="delivery-import-detail"),
    path("imports/<uuid:import_id>/apply/", MenuImportApplyView.as_view(), name="delivery-import-apply"),
]
