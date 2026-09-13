from django.urls import path

from .views import (
    AccountDetailView,
    AccountListView,
    AdjustView,
    ChargeView,
    EntriesView,
    LookupView,
    SettleView,
    StatementSendView,
    StatementsView,
    StatusView,
    SummaryView,
)

urlpatterns = [
    path("summary/", SummaryView.as_view(), name="houseaccounts-summary"),
    path("", AccountListView.as_view(), name="houseaccounts-list"),
    path("lookup/", LookupView.as_view(), name="houseaccounts-lookup"),
    path("<uuid:account_id>/", AccountDetailView.as_view(), name="houseaccounts-detail"),
    path("<uuid:account_id>/charge/", ChargeView.as_view(), name="houseaccounts-charge"),
    path("<uuid:account_id>/settle/", SettleView.as_view(), name="houseaccounts-settle"),
    path("<uuid:account_id>/adjust/", AdjustView.as_view(), name="houseaccounts-adjust"),
    path("<uuid:account_id>/status/", StatusView.as_view(), name="houseaccounts-status"),
    path("<uuid:account_id>/entries/", EntriesView.as_view(), name="houseaccounts-entries"),
    path("<uuid:account_id>/statements/", StatementsView.as_view(), name="houseaccounts-statements"),
    path(
        "<uuid:account_id>/statements/<uuid:statement_id>/send/",
        StatementSendView.as_view(),
        name="houseaccounts-statement-send",
    ),
]
