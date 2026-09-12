"""One table definition drives both the HTML page and the CSV download."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Table:
    key: str
    title: str
    columns: list[tuple[str, str]]  # (row key, header)
    rows: list[dict] = field(default_factory=list)
    money: tuple[str, ...] = ()  # keys rendered with 2 decimals + currency
    percent: tuple[str, ...] = ()
    note: str = ""

    def cells(self):
        """Rows as lists of (key, value, kind) for the template."""
        for row in self.rows:
            yield [
                (key, row.get(key), "money" if key in self.money else "percent" if key in self.percent else "text")
                for key, _ in self.columns
            ]
