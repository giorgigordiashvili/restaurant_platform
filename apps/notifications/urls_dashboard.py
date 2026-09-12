from django.urls import path

from .views import (
    DeviceView,
    MarkReadView,
    NotificationListView,
    PrefsView,
    TestMessageView,
    TestPushView,
    UnreadCountView,
)

urlpatterns = [
    path("", NotificationListView.as_view(), name="notifications-list"),
    path("unread-count/", UnreadCountView.as_view(), name="notifications-unread"),
    path("read/", MarkReadView.as_view(), name="notifications-read"),
    path("devices/", DeviceView.as_view(), name="notifications-devices"),
    path("prefs/", PrefsView.as_view(), name="notifications-prefs"),
    path("test/", TestPushView.as_view(), name="notifications-test"),
    path("test-message/", TestMessageView.as_view(), name="notifications-test-message"),
]
