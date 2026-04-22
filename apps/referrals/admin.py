"""Admin for the wallet ledger. Superuser-only."""

from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path

from unfold.admin import ModelAdmin as UnfoldModelAdmin

from apps.accounts.models import User

from .models import WalletTransaction
from .services import manual_adjustment


class ManualAdjustmentForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.all().order_by("email"))
    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Signed: positive credits the user's wallet, negative debits it.",
    )
    notes = forms.CharField(widget=forms.Textarea, required=False)


@admin.register(WalletTransaction)
class WalletTransactionAdmin(UnfoldModelAdmin):
    list_display = [
        "created_at",
        "user",
        "kind",
        "amount",
        "balance_after",
        "source_order",
        "referred_user",
        "created_by",
    ]
    list_filter = ["kind", "created_at"]
    search_fields = [
        "user__email",
        "source_order__order_number",
        "referred_user__email",
        "notes",
    ]
    date_hierarchy = "created_at"
    readonly_fields = [
        "user",
        "kind",
        "amount",
        "balance_after",
        "source_order",
        "referred_user",
        "created_by",
        "notes",
        "created_at",
        "updated_at",
    ]
    change_list_template = "admin/referrals/wallettransaction_change_list.html"

    def has_module_permission(self, request):
        return request.user.is_superuser and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        # Rows are only ever written by services / the manual-adjustment action.
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Ledger is immutable.
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "manual-adjustment/",
                self.admin_site.admin_view(self.manual_adjustment_view),
                name="referrals_wallettransaction_manual_adjustment",
            ),
        ]
        return custom + urls

    def manual_adjustment_view(self, request):
        if not request.user.is_superuser:
            messages.error(request, "Superuser-only.")
            return redirect("admin:referrals_wallettransaction_changelist")

        if request.method == "POST":
            form = ManualAdjustmentForm(request.POST)
            if form.is_valid():
                txn = manual_adjustment(
                    user=form.cleaned_data["user"],
                    amount=Decimal(form.cleaned_data["amount"]),
                    created_by=request.user,
                    notes=form.cleaned_data["notes"],
                )
                messages.success(
                    request,
                    f"Wallet of {txn.user.email} adjusted by {txn.amount}; new balance {txn.balance_after}.",
                )
                return redirect("admin:referrals_wallettransaction_changelist")
        else:
            form = ManualAdjustmentForm()

        return render(
            request,
            "admin/referrals/manual_adjustment_form.html",
            {"form": form, "title": "Manual wallet adjustment"},
        )
