"""CSV export of any report table."""

from __future__ import annotations

import csv
from decimal import Decimal

from django.http import HttpResponse


def csv_response(filename: str, columns: list[tuple[str, str]], rows: list[dict]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # Excel-friendly BOM for Georgian text
    writer = csv.writer(response)
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([_cell(row.get(key)) for key, _ in columns])
    return response


def _cell(value):
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return ""
    return value
