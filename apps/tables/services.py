"""Table sessions shared by orders, reservations and the waitlist."""

from __future__ import annotations


def open_session(table, by=None, *, party_size: int | None = None):
    """The table's active session, or a new one (host = ``by`` when a signed-in user); marks the table occupied."""
    from apps.tables.models import TableSession, TableSessionGuest

    session = table.sessions.filter(status="active").first()
    if session is None:
        session = TableSession.objects.create(
            table=table,
            host=by if getattr(by, "is_authenticated", False) else None,
            guest_count=max(int(party_size or 1), 1),
        )
        if session.host_id:
            TableSessionGuest.objects.create(session=session, user=session.host, is_host=True)
        table.set_occupied()
    elif party_size and session.guest_count < party_size:
        session.guest_count = party_size
        session.save(update_fields=["guest_count", "updated_at"])
    return session
