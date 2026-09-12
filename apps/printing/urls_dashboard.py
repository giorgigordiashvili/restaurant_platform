from django.urls import path

from .views import (
    PrinterDetailView,
    PrinterListCreateView,
    PrinterRotateKeyView,
    PrinterTestView,
    PrintJobCreateView,
    PrintJobListView,
    PrintJobRetryView,
)

app_name = "printing_dashboard"

urlpatterns = [
    path("printers/", PrinterListCreateView.as_view(), name="printer-list"),
    path("printers/<uuid:id>/", PrinterDetailView.as_view(), name="printer-detail"),
    path("printers/<uuid:id>/rotate-key/", PrinterRotateKeyView.as_view(), name="printer-rotate-key"),
    path("printers/<uuid:id>/test/", PrinterTestView.as_view(), name="printer-test"),
    path("jobs/", PrintJobListView.as_view(), name="job-list"),
    path("jobs/create/", PrintJobCreateView.as_view(), name="job-create"),
    path("jobs/<uuid:id>/retry/", PrintJobRetryView.as_view(), name="job-retry"),
]
