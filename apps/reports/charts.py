"""
Chart.js payloads for Unfold's ``unfold/components/chart/{line,bar}.html``:
the component drops the JSON into ``data-value`` and Unfold's app.js builds
the chart, resolving ``var(--color-...)`` colours against the theme.
"""

from __future__ import annotations

import json
from decimal import Decimal

PALETTE = (
    "var(--color-primary-600)",
    "var(--color-primary-300)",
    "var(--color-base-500)",
    "var(--color-base-300)",
    "var(--color-primary-800)",
)


def _num(v):
    if isinstance(v, Decimal):
        return float(v)
    return v if v is not None else 0


def dataset(label: str, values, colour: str, *, fill: bool = False) -> dict:
    return {
        "label": label,
        "data": [_num(v) for v in values],
        "borderColor": colour,
        "backgroundColor": colour,
        "fill": fill,
        "tension": 0.3,
    }


def chart(labels, datasets) -> str:
    return json.dumps({"labels": list(labels), "datasets": datasets})


def line(labels, series: list[tuple[str, list]]) -> str:
    return chart(
        labels, [dataset(label, values, PALETTE[i % len(PALETTE)]) for i, (label, values) in enumerate(series)]
    )


def bar(labels, series: list[tuple[str, list]]) -> str:
    return chart(
        labels, [dataset(label, values, PALETTE[i % len(PALETTE)]) for i, (label, values) in enumerate(series)]
    )
