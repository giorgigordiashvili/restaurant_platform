from django.urls import path

from .views import ClockView, EntriesView, MyShiftsView, RotaView, WhosInView

urlpatterns = [
    path("clock/", ClockView.as_view(), name="timekeeping-clock"),
    path("whos-in/", WhosInView.as_view(), name="timekeeping-whos-in"),
    path("entries/", EntriesView.as_view(), name="timekeeping-entries"),
    path("rota/", RotaView.as_view(), name="timekeeping-rota"),
    path("rota/mine/", MyShiftsView.as_view(), name="timekeeping-my-shifts"),
]
