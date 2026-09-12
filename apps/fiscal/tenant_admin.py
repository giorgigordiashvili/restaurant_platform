"""
Fiscal settings page (one form: legal data, VAT flags, provider, RS.ge
credentials) and the fiscal document list with retry / XML download.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.fiscal import services
from apps.fiscal.models import FiscalDocument, FiscalProfile, FiscalSettingsPage
from apps.fiscal.providers import get_provider
from apps.fiscal.providers.rsge.waybill_xml import build_waybill_xml


class FiscalEnabledMixin(ModuleEnabledMixin):
    module_code = "fiscal"


class FiscalProfileForm(forms.ModelForm):
    service_user = forms.CharField(required=False, label="RS.ge service user")
    service_password = forms.CharField(
        required=False, label="RS.ge service password", widget=forms.PasswordInput(render_value=False)
    )

    class Meta:
        model = FiscalProfile
        fields = [
            "legal_name",
            "tax_id",
            "legal_address",
            "vat_payer",
            "vat_rate",
            "prices_include_vat",
            "provider",
            "receipt_prefix",
            "receipt_footer",
        ]


class FiscalSettingsTenantAdmin(FiscalEnabledMixin, TenantModelAdmin):
    permission_resource = "fiscal"
    restaurant_field = "restaurant"
    change_list_template = "admin/fiscal/fiscalsettingspage/change_list.html"
    list_display = ["id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        raise PermissionDenied

    def changelist_view(self, request, extra_context=None):
        if not getattr(request, "restaurant", None) or not self.has_view_permission(request):
            raise PermissionDenied
        profile = services.profile_for(request.restaurant)
        creds = profile.get_credentials()
        form = FiscalProfileForm(instance=profile, initial={"service_user": creds.get("service_user", "")})
        extra_context = dict(extra_context or {})
        extra_context.update(
            {
                "form": form,
                "profile": profile,
                "has_password": bool(creds.get("service_password")),
                "can_manage": has_resource_permission(request, "fiscal", "update"),
                "save_url": reverse("tenant_admin:fiscal_fiscalsettingspage_save"),
                "test_url": reverse("tenant_admin:fiscal_fiscalsettingspage_test"),
                "documents_url": reverse("tenant_admin:fiscal_fiscaldocument_changelist"),
                "counts": {
                    "confirmed": FiscalDocument.objects.filter(
                        restaurant=request.restaurant, status="confirmed"
                    ).count(),
                    "failed": FiscalDocument.objects.filter(restaurant=request.restaurant, status="failed").count(),
                    "drafts": FiscalDocument.objects.filter(restaurant=request.restaurant, status="draft").count(),
                },
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("save/", wrap(require_POST(self.save_view)), name="fiscal_fiscalsettingspage_save"),
            path("test-connection/", wrap(require_POST(self.test_view)), name="fiscal_fiscalsettingspage_test"),
        ]
        return custom + super().get_urls()

    def _guard(self, request):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "fiscal", "update"):
            raise PermissionDenied

    def save_view(self, request):
        self._guard(request)
        profile = services.profile_for(request.restaurant)
        form = FiscalProfileForm(request.POST, instance=profile)
        if not form.is_valid():
            for field, errors in form.errors.items():
                messages.error(request, f"{field}: {'; '.join(errors)}")
            return redirect("tenant_admin:fiscal_fiscalsettingspage_changelist")
        profile = form.save(commit=False)
        creds = profile.get_credentials()
        user = form.cleaned_data.get("service_user", "").strip()
        password = form.cleaned_data.get("service_password", "")
        if user != creds.get("service_user", ""):
            creds["service_user"] = user
        if password:
            creds["service_password"] = password
        if not user:
            creds.pop("service_user", None)
            creds.pop("service_password", None)
        profile.set_credentials(creds)
        profile.save()
        _audit(
            request,
            "Fiscal settings updated",
            {k: str(v) for k, v in form.cleaned_data.items() if k != "service_password"},
        )
        messages.success(request, "Fiscal settings saved.")
        return redirect("tenant_admin:fiscal_fiscalsettingspage_changelist")

    def test_view(self, request):
        self._guard(request)
        profile = services.profile_for(request.restaurant)
        result = get_provider(profile).health()
        if result.ok:
            messages.success(request, "Provider connection OK.")
        else:
            messages.error(request, f"Provider check failed: {result.error}")
        return redirect("tenant_admin:fiscal_fiscalsettingspage_changelist")


class FiscalDocumentTenantAdmin(FiscalEnabledMixin, TenantModelAdmin):
    permission_resource = "fiscal"
    list_display = [
        "fiscal_number",
        "kind",
        "status_chip",
        "gross_total",
        "vat_total",
        "order",
        "attempts",
        "created_at",
    ]
    list_filter = ["kind", "status"]
    search_fields = ["fiscal_number", "external_id", "order__order_number"]
    ordering = ["-created_at"]
    actions_row = ["download_xml", "retry_document", "cancel_document"]

    @action(description="XML", url_path="xml", attrs={"target": "_blank"})
    def download_xml(self, request, object_id):
        return self.xml_view(request, object_id)

    @action(description="Retry / send", url_path="retry")
    def retry_document(self, request, object_id):
        return self.retry_view(request, object_id)

    @action(description="Cancel draft", url_path="cancel")
    def cancel_document(self, request, object_id):
        return self.cancel_view(request, object_id)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("order", "payment")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in FiscalDocument._meta.fields if f.name != "id"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description="Status")
    def status_chip(self, obj):
        colours = {
            "confirmed": "bg-green-100 text-green-700",
            "failed": "bg-red-100 text-red-700",
            "queued": "bg-blue-100 text-blue-700",
            "sent": "bg-blue-100 text-blue-700",
            "draft": "bg-base-100 text-base-600",
            "cancelled": "bg-base-100 text-base-500",
        }
        return format_html(
            '<span class="text-xs uppercase px-2 py-0.5 rounded {}" data-testid="doc-status">{}</span>',
            colours.get(obj.status, ""),
            obj.get_status_display(),
        )

    def _doc(self, request, pk, action="read"):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "fiscal", action):
            raise PermissionDenied
        doc = FiscalDocument.objects.filter(pk=pk, restaurant=request.restaurant).first()
        if doc is None:
            raise Http404
        return doc

    def xml_view(self, request, pk):
        doc = self._doc(request, pk)
        if doc.kind not in ("waybill_in", "waybill_out"):
            raise Http404
        response = HttpResponse(build_waybill_xml(doc), content_type="application/xml")
        response["Content-Disposition"] = f'attachment; filename="waybill-{doc.fiscal_number or doc.pk}.xml"'
        return response

    def retry_view(self, request, pk):
        doc = self._doc(request, pk, "update")
        services.retry(doc)
        messages.success(request, f"{doc.fiscal_number} queued again.")
        return redirect("tenant_admin:fiscal_fiscaldocument_changelist")

    def cancel_view(self, request, pk):
        doc = self._doc(request, pk, "update")
        try:
            services.cancel(doc)
            messages.success(request, f"{doc.fiscal_number} cancelled.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("tenant_admin:fiscal_fiscaldocument_changelist")


def _audit(request, description, changes):
    try:
        from apps.audit.services import log_action

        log_action(
            "settings_update",
            request=request,
            restaurant=request.restaurant,
            description=description,
            target_model="FiscalProfile",
            changes=changes,
        )
    except Exception:  # pragma: no cover
        pass


def register_fiscal_admin(site):
    site.register(FiscalSettingsPage, FiscalSettingsTenantAdmin)
    site.register(FiscalDocument, FiscalDocumentTenantAdmin)
