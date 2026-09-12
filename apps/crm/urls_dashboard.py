from django.urls import path

from .views import (
    CampaignListView,
    CustomerConsentView,
    CustomerDetailView,
    CustomerListView,
    CustomerLookupView,
    SegmentListView,
    SummaryView,
)

urlpatterns = [
    path("summary/", SummaryView.as_view(), name="crm-summary"),
    path("customers/", CustomerListView.as_view(), name="crm-customers"),
    path("customers/lookup/", CustomerLookupView.as_view(), name="crm-lookup"),
    path("customers/<uuid:customer_id>/", CustomerDetailView.as_view(), name="crm-customer"),
    path("customers/<uuid:customer_id>/consent/", CustomerConsentView.as_view(), name="crm-consent"),
    path("segments/", SegmentListView.as_view(), name="crm-segments"),
    path("campaigns/", CampaignListView.as_view(), name="crm-campaigns"),
]
