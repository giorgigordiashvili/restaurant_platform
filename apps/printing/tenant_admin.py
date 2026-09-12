"""
Printers and print jobs in the tenant admin: add a printer, copy its bridge
key and the one-line install command, print a test page, retry failed jobs.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.printing import services
from apps.printing.models import Printer, PrintJob


class PrintingEnabledMixin(ModuleEnabledMixin):
    module_code = "printing"


class PrinterTenantAdmin(PrintingEnabledMixin, TenantModelAdmin):
    permission_resource = "settings"
    list_display = [
        "name",
        "kind",
        "stations",
        "paper",
        "connection",
        "online",
        "auto_print",
        "is_active",
        "last_error",
    ]
    list_filter = ["kind", "connection", "is_active"]
    search_fields = ["name"]
    fields = [
        "name",
        "kind",
        "stations",
        "paper",
        "connection",
        "copies",
        "auto_print",
        "open_drawer",
        "is_active",
        "setup",
        "last_seen_at",
        "last_error",
    ]
    readonly_fields = ["setup", "last_seen_at", "last_error"]
    actions = ["print_test_page", "rotate_bridge_key"]
    actions_row = ["print_test_page_row"]

    @display(description=_("Bridge"), boolean=True)
    def online(self, obj):
        return obj.connection != "bridge" or obj.is_online

    @display(description=_("Print bridge setup"))
    def setup(self, obj):
        if not obj.pk:
            return "Save the printer first; the bridge key appears here."
        if obj.connection != "bridge":
            return "Browser printing: the POS prints through the tablet's own print dialog; no bridge needed."
        base = getattr(settings, "PUBLIC_API_BASE_URL", "https://admin.aimenu.ge")
        return format_html(
            '<div class="text-sm" data-testid="bridge-setup">'
            "<p>1. On the computer next to the printer install the bridge (see tools/print_bridge/README.md).</p>"
            "<p>2. Put this in <code>bridge.toml</code>:</p>"
            '<pre class="bg-base-100 dark:bg-base-800 rounded-default p-2 mt-1 mb-2 text-xs">[bridge]\n'
            'server_url = "{}"\nkey = "{}"\nprinter = "usb://"   # or net://192.168.1.50:9100, serial:///dev/rfcomm0, win://PrinterName\n'
            'paper = "{}"</pre>'
            "<p>3. Run <code>python aimenu_print_bridge.py</code>. This page shows &quot;online&quot; within a minute.</p>"
            "</div>",
            base,
            obj.bridge_key,
            obj.paper,
        )

    def get_actions(self, request):
        actions_ = super().get_actions(request)
        if not has_resource_permission(request, "settings", "update"):
            return {}
        return actions_

    @action(description=_("Print a test page"))
    def print_test_page(self, request, queryset):
        n = 0
        for printer in queryset:
            if printer.connection == "bridge":
                services.enqueue_test(printer, by=request.user)
                n += 1
        messages.success(request, _("{p0} test page(s) queued.").format(p0=n))

    @action(description=_("Print test page"), url_path="print-test", attrs={"target": "_self"})
    def print_test_page_row(self, request, object_id):
        from django.shortcuts import redirect

        printer = self.get_queryset(request).filter(pk=object_id).first()
        if printer is None or not has_resource_permission(request, "settings", "update"):
            messages.error(request, _("Not allowed."))
        else:
            services.enqueue_test(printer, by=request.user)
            messages.success(request, _("Test page queued on {p0}.").format(p0=printer.name))
        return redirect("tenant_admin:printing_printer_changelist")

    @action(description=_("Rotate bridge key (old key stops working)"))
    def rotate_bridge_key(self, request, queryset):
        for printer in queryset:
            printer.rotate_key()
        messages.success(request, _("Bridge key rotated. Update bridge.toml on the printer's computer."))


class PrintJobTenantAdmin(PrintingEnabledMixin, TenantModelAdmin):
    permission_resource = "orders"
    list_display = ["title", "printer", "kind", "status", "attempts", "error", "created_at", "printed_at"]
    list_filter = ["status", "kind", "printer"]
    search_fields = ["title", "order__order_number"]
    ordering = ["-created_at"]
    actions = ["retry_jobs"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("printer", "order")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in PrintJob._meta.fields if f.name not in ("id", "escpos")]

    def get_exclude(self, request, obj=None):
        return [*(super().get_exclude(request, obj) or []), "escpos"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return has_resource_permission(request, "orders", "delete")

    @action(description=_("Retry (queue again)"))
    def retry_jobs(self, request, queryset):
        n = 0
        for job in queryset:
            services.retry(job)
            n += 1
        messages.success(request, _("{p0} job(s) queued again.").format(p0=n))


def register_printing_admin(site):
    site.register(Printer, PrinterTenantAdmin)
    site.register(PrintJob, PrintJobTenantAdmin)
