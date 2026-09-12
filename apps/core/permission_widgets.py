"""
Checkbox grid for StaffRole.permissions / StaffMember.permissions_override.

The JSON shape stays ``{resource: [actions]}``; the form field renders one
row per resource the restaurant's modules offer and rebuilds the dict from
``<name>__<resource>__<action>`` checkboxes.
"""

from __future__ import annotations

from django import forms

ACTIONS = (("read", "View"), ("create", "Create"), ("update", "Edit"), ("delete", "Delete"))

RESOURCE_LABELS = {
    "menu": "Menu",
    "orders": "Orders",
    "tables": "Tables & QR",
    "reservations": "Reservations",
    "warehouse": "Warehouse",
    "warehouse_logs": "Warehouse logs (waste, meals)",
    "cash": "Cash & payments",
    "staff": "Staff",
    "settings": "Settings",
    "analytics": "Analytics",
}

# Cells that make no sense for a resource are simply not offered.
OFFERED = {"settings": ("read", "update"), "analytics": ("read",)}

# What each cell means, shown as a tooltip where the generic verb is unclear.
HINTS = {
    "cash": {
        "read": "See the open shift, X report and payments",
        "create": "Take payments, open a shift, cash paid in / out",
        "update": "Discounts, comps, close a shift",
        "delete": "Refunds",
    },
    "orders": {"update": "Change status, add items, void items"},
}


def rows_for(restaurant):
    from apps.core.modules import resources_available

    return [(r, RESOURCE_LABELS.get(r, r.replace("_", " ").capitalize())) for r in resources_available(restaurant)]


class PermissionMatrixWidget(forms.Widget):
    template_name = "admin/widgets/permission_matrix.html"

    def __init__(self, rows, attrs=None):
        super().__init__(attrs)
        self.rows = rows

    def format_value(self, value):
        return value or {}

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        value = value if isinstance(value, dict) else {}
        context["widget"]["actions"] = ACTIONS
        context["widget"]["rows"] = [
            {
                "resource": resource,
                "label": label,
                "cells": [
                    {
                        "action": action,
                        "name": f"{name}__{resource}__{action}",
                        "checked": action in value.get(resource, []) or "*" in value.get(resource, []),
                        "offered": action in OFFERED.get(resource, tuple(a for a, _ in ACTIONS)),
                        "hint": HINTS.get(resource, {}).get(action, ""),
                    }
                    for action, _ in ACTIONS
                ],
            }
            for resource, label in self.rows
        ]
        return context

    def value_from_datadict(self, data, files, name):
        out = {}
        for resource, _ in self.rows:
            actions = [a for a, _ in ACTIONS if data.get(f"{name}__{resource}__{a}")]
            if actions:
                out[resource] = actions
        return out

    def value_omitted_from_data(self, data, files, name):
        return False


class PermissionMatrixField(forms.Field):
    """
    ``keep`` holds the permissions of resources not shown in the grid
    (modules currently switched off) so saving never wipes them.
    """

    def __init__(self, rows, *, keep=None, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(widget=PermissionMatrixWidget(rows), **kwargs)
        self.keep = dict(keep or {})

    def to_python(self, value):
        return dict(value) if isinstance(value, dict) else {}

    def clean(self, value):
        return {**self.keep, **self.to_python(value)}
