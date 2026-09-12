from django.urls import path

from .views import MenuSyncView, PauseStoreView, PlatformListView, ResumeStoreView, StoreStatusView

urlpatterns = [
    path("platforms/", PlatformListView.as_view(), name="delivery-platforms"),
    path("platforms/<slug:code>/status/", StoreStatusView.as_view(), name="delivery-platform-status"),
    path("platforms/<slug:code>/pause/", PauseStoreView.as_view(), name="delivery-platform-pause"),
    path("platforms/<slug:code>/resume/", ResumeStoreView.as_view(), name="delivery-platform-resume"),
    path("platforms/<slug:code>/menu-sync/", MenuSyncView.as_view(), name="delivery-platform-menu-sync"),
]
