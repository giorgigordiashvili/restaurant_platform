"""
All venue state changes go through here: share requests, venue creation,
registry edits and the mirroring of the registry into each member's own
Table / TableSection rows. Views (REST and admin) only translate errors.
"""

import logging
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.tables.models import Table, TableQRCode, TableSection
from apps.tenants.models import Restaurant

from .models import Venue, VenueMember, VenueSection, VenueShareRequest, VenueTable

logger = logging.getLogger(__name__)

# Registry-owned fields on a mirrored row. Everything else (status, position,
# is_active) stays the member restaurant's own.
MANAGED_TABLE_FIELDS = ("number", "name", "capacity", "min_capacity", "shape")
MANAGED_SECTION_FIELDS = ("name",)

LAYOUT_OURS = "ours"
LAYOUT_THEIRS = "theirs"


class VenueError(Exception):
    """A business-rule failure; ``status_code`` maps to the HTTP response."""

    def __init__(self, message, code="venue_error", status_code=400, field_name=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.field_name = field_name


@dataclass
class SyncSummary:
    created: int = 0
    linked: int = 0
    updated: int = 0
    conflicts: list = field(default_factory=list)

    def merge(self, other):
        self.created += other.created
        self.linked += other.linked
        self.updated += other.updated
        self.conflicts.extend(other.conflicts)
        return self

    def as_dict(self):
        return {"created": self.created, "linked": self.linked, "updated": self.updated, "conflicts": self.conflicts}


# --------------------------------------------------------------------------- lookups


def get_membership(restaurant):
    """The restaurant's VenueMember (venue preloaded) or None."""
    if restaurant is None:
        return None
    return VenueMember.objects.select_related("venue").filter(restaurant=restaurant).first()


def venue_for_table(table):
    """(venue, venue_table) when the table mirrors an active venue table, else (None, None)."""
    if table.venue_table_id is None:
        return None, None
    venue_table = table.venue_table
    if not venue_table.is_active or not venue_table.venue.is_active:
        return None, None
    return venue_table.venue, venue_table


def member_table(venue_table, restaurant):
    """The restaurant's own mirrored Table for a venue table, or None."""
    return Table.objects.filter(restaurant=restaurant, venue_table=venue_table).select_related("section").first()


def table_code(table):
    """The first active QR code on a table, or None."""
    if table is None:
        return None
    qr = table.qr_codes.filter(is_active=True).order_by("created_at").first()
    return qr.code if qr else None


def member_tables_for(venue_table):
    """{restaurant_id: Table} for every active member of the venue table's venue."""
    tables = (
        Table.objects.filter(venue_table=venue_table, is_active=True)
        .select_related("restaurant")
        .prefetch_related("qr_codes")
    )
    return {t.restaurant_id: t for t in tables}


# --------------------------------------------------------------------------- requests


def _restaurant_by_slug(slug):
    return Restaurant.objects.filter(slug=slug, is_active=True).first()


def send_share_request(from_restaurant, to_slug, user, venue_name="", message=""):
    # Unknown, inactive and self all read the same, so slugs cannot be probed.
    not_found = VenueError("Restaurant not found.", code="restaurant_not_found", field_name="to_restaurant")
    to_restaurant = _restaurant_by_slug((to_slug or "").strip())
    if to_restaurant is None or to_restaurant.pk == from_restaurant.pk:
        raise not_found

    from_member = get_membership(from_restaurant)
    to_member = get_membership(to_restaurant)
    if from_member and to_member:
        if from_member.venue_id == to_member.venue_id:
            raise VenueError("Already sharing a venue with that restaurant.", code="already_member")
        raise VenueError("That restaurant already belongs to another venue.", code="already_in_venue", status_code=409)
    if to_member and not from_member and not to_member.venue.is_active:
        raise VenueError("That restaurant already belongs to another venue.", code="already_in_venue", status_code=409)

    pending = VenueShareRequest.objects.filter(
        Q(from_restaurant=from_restaurant, to_restaurant=to_restaurant)
        | Q(from_restaurant=to_restaurant, to_restaurant=from_restaurant),
        status=VenueShareRequest.STATUS_PENDING,
        expires_at__gt=timezone.now(),
    ).exists()
    if pending:
        raise VenueError(
            "A request between these restaurants is already pending.", code="request_pending", status_code=409
        )

    try:
        with transaction.atomic():
            req = VenueShareRequest.objects.create(
                from_restaurant=from_restaurant,
                to_restaurant=to_restaurant,
                requested_by=user,
                venue_name=(venue_name or "").strip(),
                message=(message or "").strip(),
            )
    except IntegrityError:
        raise VenueError(
            "A request between these restaurants is already pending.", code="request_pending", status_code=409
        )

    from .emails import send_share_request_email

    send_share_request_email(req)
    return req


def layout_options(req):
    """Which layouts the acceptor may choose: only 'theirs' when a venue already exists."""
    if get_membership(req.from_restaurant) or get_membership(req.to_restaurant):
        return [LAYOUT_THEIRS]
    return [LAYOUT_OURS, LAYOUT_THEIRS]


def _pending_request_for(request_id, **scope):
    req = (
        VenueShareRequest.objects.select_related("from_restaurant", "to_restaurant")
        .filter(id=request_id, status=VenueShareRequest.STATUS_PENDING, **scope)
        .first()
    )
    if req is None:
        raise VenueError("Request not found.", code="request_not_found", status_code=404)
    if req.is_expired:
        req.status = VenueShareRequest.STATUS_EXPIRED
        req.save(update_fields=["status", "updated_at"])
        raise VenueError("This request has expired.", code="request_expired", status_code=410)
    return req


def accept_share_request(request_id, acceptor, user, layout=None, venue_name=""):
    """
    Accept as ``acceptor`` (must be the request's target).

    Returns (venue, SyncSummary). ``layout`` is "ours" / "theirs" and is only
    honoured when the acceptance creates the venue.
    """
    with transaction.atomic():
        req = _pending_request_for(request_id, to_restaurant=acceptor)
        sender = req.from_restaurant

        # Lock both restaurants (ordered by pk) so two concurrent accepts serialise.
        locked = list(Restaurant.objects.select_for_update().filter(pk__in=[sender.pk, acceptor.pk]).order_by("pk"))
        assert len(locked) == 2

        sender_member = get_membership(sender)
        acceptor_member = get_membership(acceptor)

        if sender_member and acceptor_member:
            raise VenueError("Both restaurants already belong to a venue.", code="already_in_venue", status_code=409)

        summary = SyncSummary()
        if sender_member or acceptor_member:
            existing = (sender_member or acceptor_member).venue
            if not existing.is_active:
                raise VenueError("That venue is no longer active.", code="venue_dissolved", status_code=410)
            if layout not in (None, "", LAYOUT_THEIRS):
                raise VenueError(
                    "The venue already has a layout; new members adopt it.",
                    code="layout_source_fixed",
                    field_name="layout",
                )
            joiner = acceptor if sender_member else sender
            venue = existing
            _add_member(venue, joiner)
            summary.merge(sync_member(venue, joiner))
        else:
            if layout not in (LAYOUT_OURS, LAYOUT_THEIRS):
                raise VenueError(
                    "Choose whose table layout the venue starts from.", code="layout_required", field_name="layout"
                )
            seed = acceptor if layout == LAYOUT_OURS else sender
            other = sender if seed is acceptor else acceptor
            name = (venue_name or req.venue_name or "").strip() or f"{sender.name} & {acceptor.name}"
            venue = Venue.objects.create(name=name, created_by=user)
            _add_member(venue, seed, is_layout_seed=True)
            _add_member(venue, other)
            seed_registry(venue, seed)
            summary.merge(sync_member(venue, seed, report_links=False))
            summary.merge(sync_member(venue, other))

        req.status = VenueShareRequest.STATUS_ACCEPTED
        req.responded_by = user
        req.responded_at = timezone.now()
        req.save(update_fields=["status", "responded_by", "responded_at", "updated_at"])
        # A request in the other direction is now moot.
        VenueShareRequest.objects.filter(
            from_restaurant=acceptor, to_restaurant=sender, status=VenueShareRequest.STATUS_PENDING
        ).update(status=VenueShareRequest.STATUS_CANCELLED, responded_at=timezone.now())

    from .emails import send_share_response_email

    send_share_response_email(req)
    return venue, summary


def decline_share_request(request_id, restaurant, user):
    with transaction.atomic():
        req = _pending_request_for(request_id, to_restaurant=restaurant)
        req.status = VenueShareRequest.STATUS_DECLINED
        req.responded_by = user
        req.responded_at = timezone.now()
        req.save(update_fields=["status", "responded_by", "responded_at", "updated_at"])
    from .emails import send_share_response_email

    send_share_response_email(req)
    return req


def cancel_share_request(request_id, restaurant, user):
    with transaction.atomic():
        req = _pending_request_for(request_id, from_restaurant=restaurant)
        req.status = VenueShareRequest.STATUS_CANCELLED
        req.responded_by = user
        req.responded_at = timezone.now()
        req.save(update_fields=["status", "responded_by", "responded_at", "updated_at"])
    return req


def _add_member(venue, restaurant, is_layout_seed=False):
    order = venue.memberships.aggregate(m=Max("display_order"))["m"]
    try:
        return VenueMember.objects.create(
            venue=venue,
            restaurant=restaurant,
            display_order=0 if order is None else order + 1,
            is_layout_seed=is_layout_seed,
        )
    except IntegrityError:
        raise VenueError("That restaurant already belongs to a venue.", code="already_in_venue", status_code=409)


# --------------------------------------------------------------------------- registry


def seed_registry(venue, restaurant):
    """Copy the restaurant's active sections and tables into the venue registry."""
    section_map = {}
    for section in TableSection.objects.filter(restaurant=restaurant, is_active=True).order_by("display_order", "name"):
        vs, _ = VenueSection.objects.get_or_create(
            venue=venue,
            name=section.name.strip(),
            defaults={"description": section.description, "display_order": section.display_order},
        )
        section_map[section.pk] = vs
    for table in Table.objects.filter(restaurant=restaurant, is_active=True).order_by("number"):
        VenueTable.objects.get_or_create(
            venue=venue,
            number=table.number.strip(),
            defaults={
                "section": section_map.get(table.section_id),
                "name": table.name,
                "capacity": table.capacity,
                "min_capacity": table.min_capacity,
                "shape": table.shape,
            },
        )


def sync_member(venue, restaurant, report_links=True):
    """
    Mirror the venue registry into ``restaurant``'s own sections and tables.

    Idempotent. Links by case-insensitive number/name, adopting a member's
    existing rows rather than renaming or deleting anything; a mirror that is
    already tied to a *different* venue table aborts the whole sync.
    """
    summary = SyncSummary()
    with transaction.atomic():
        # order_by(pk): the models' default ordering joins section, and Postgres
        # will not FOR UPDATE across an outer join.
        own_sections = list(TableSection.objects.select_for_update().filter(restaurant=restaurant).order_by("pk"))
        own_tables = list(Table.objects.select_for_update().filter(restaurant=restaurant).order_by("pk"))

        section_by_vs = {s.venue_section_id: s for s in own_sections if s.venue_section_id}
        section_by_name = {s.name.strip().lower(): s for s in own_sections}
        for vs in venue.sections.filter(is_active=True):
            mirror = section_by_vs.get(vs.pk) or section_by_name.get(vs.name.strip().lower())
            if mirror is None:
                mirror = TableSection.objects.create(
                    restaurant=restaurant,
                    name=vs.name,
                    description=vs.description,
                    display_order=vs.display_order,
                    venue_section=vs,
                )
                section_by_name[vs.name.strip().lower()] = mirror
            else:
                changed = []
                if mirror.venue_section_id != vs.pk:
                    if mirror.venue_section_id is not None:
                        raise VenueError(
                            f"Section '{mirror.name}' at {restaurant.name} is already linked to another venue section.",
                            code="section_conflict",
                            status_code=409,
                        )
                    mirror.venue_section = vs
                    changed.append("venue_section")
                if mirror.display_order != vs.display_order:
                    mirror.display_order = vs.display_order
                    changed.append("display_order")
                if changed:
                    mirror.save(update_fields=changed + ["updated_at"])
            section_by_vs[vs.pk] = mirror

        table_by_vt = {t.venue_table_id: t for t in own_tables if t.venue_table_id}
        table_by_number = {t.number.strip().lower(): t for t in own_tables}
        for vt in venue.tables.filter(is_active=True).select_related("section"):
            target_section = section_by_vs.get(vt.section_id) if vt.section_id else None
            mirror = table_by_vt.get(vt.pk) or table_by_number.get(vt.number.strip().lower())
            if mirror is None:
                mirror = Table.objects.create(
                    restaurant=restaurant,
                    number=vt.number,
                    name=vt.name,
                    capacity=vt.capacity,
                    min_capacity=vt.min_capacity,
                    shape=vt.shape,
                    section=target_section,
                    venue_table=vt,
                )
                table_by_number[vt.number.strip().lower()] = mirror
                summary.created += 1
            else:
                if mirror.venue_table_id not in (None, vt.pk):
                    raise VenueError(
                        f"Table '{mirror.number}' at {restaurant.name} is already linked to another venue table.",
                        code="table_conflict",
                        status_code=409,
                    )
                changed = []
                if mirror.venue_table_id is None:
                    mirror.venue_table = vt
                    changed.append("venue_table")
                    if report_links:
                        summary.linked += 1
                        summary.conflicts.append(
                            {"number": mirror.number, "restaurant": restaurant.slug, "resolution": "linked_existing"}
                        )
                for f in ("name", "capacity", "min_capacity", "shape"):
                    if getattr(mirror, f) != getattr(vt, f):
                        setattr(mirror, f, getattr(vt, f))
                        changed.append(f)
                if mirror.section_id != (target_section.pk if target_section else None):
                    mirror.section = target_section
                    changed.append("section")
                if changed:
                    mirror.save(update_fields=changed + ["updated_at"])
                    if "venue_table" not in changed:
                        summary.updated += 1
            table_by_vt[vt.pk] = mirror
            if not mirror.qr_codes.exists():
                TableQRCode.objects.create(table=mirror)
    return summary


def sync_all(venue):
    summary = SyncSummary()
    for member in venue.memberships.select_related("restaurant"):
        summary.merge(sync_member(venue, member.restaurant))
    return summary


def _check_number_free(venue, number, exclude_pk=None):
    """A registry number must not collide with a member's *private* table."""
    clash = venue.tables.filter(number__iexact=number.strip()).exclude(pk=exclude_pk).exists()
    if clash:
        raise VenueError(f"Table {number} already exists in the venue.", code="number_taken", field_name="number")
    for member in venue.memberships.select_related("restaurant"):
        private = Table.objects.filter(
            restaurant=member.restaurant, number__iexact=number.strip(), venue_table__isnull=True
        )
        if private.exists():
            raise VenueError(
                f"{member.restaurant.name} already has its own table {number}; rename it there first.",
                code="number_conflict",
                status_code=409,
                field_name="number",
            )


def create_venue_table(venue, **fields):
    number = (fields.get("number") or "").strip()
    if not number:
        raise VenueError("Table number is required.", code="number_required", field_name="number")
    with transaction.atomic():
        _check_number_free(venue, number)
        fields["number"] = number
        vt = VenueTable.objects.create(venue=venue, **fields)
        summary = sync_all(venue)
    return vt, summary


def update_venue_table(vt, **fields):
    with transaction.atomic():
        if "number" in fields and fields["number"].strip() != vt.number:
            _check_number_free(vt.venue, fields["number"], exclude_pk=vt.pk)
            fields["number"] = fields["number"].strip()
        reactivating = fields.get("is_active") is True and not vt.is_active
        for k, v in fields.items():
            setattr(vt, k, v)
        vt.save()
        if reactivating:
            Table.objects.filter(venue_table=vt).update(is_active=True)
        summary = sync_all(vt.venue)
    return vt, summary


def deactivate_venue_table(vt):
    """Retire a shared table: mirrors go inactive unless a session is live on them."""
    with transaction.atomic():
        vt.is_active = False
        vt.save(update_fields=["is_active", "updated_at"])
        skipped = []
        for mirror in Table.objects.filter(venue_table=vt).select_related("restaurant"):
            if mirror.sessions.filter(status="active").exists():
                skipped.append(mirror.restaurant.slug)
                continue
            mirror.is_active = False
            mirror.save(update_fields=["is_active", "updated_at"])
    return skipped


def create_venue_section(venue, **fields):
    with transaction.atomic():
        vs = VenueSection.objects.create(venue=venue, **fields)
        summary = sync_all(venue)
    return vs, summary


def update_venue_section(vs, **fields):
    with transaction.atomic():
        for k, v in fields.items():
            setattr(vs, k, v)
        vs.save()
        # A renamed section propagates by link, not by name.
        TableSection.objects.filter(venue_section=vs).update(name=vs.name, display_order=vs.display_order)
        summary = sync_all(vs.venue)
    return vs, summary


# --------------------------------------------------------------------------- leaving


def leave_venue(restaurant):
    """Unlink the restaurant; its tables stay as private tables, nothing is deleted."""
    with transaction.atomic():
        member = get_membership(restaurant)
        if member is None:
            raise VenueError("This restaurant is not part of a venue.", code="not_a_member", status_code=404)
        venue = member.venue
        Table.objects.filter(restaurant=restaurant).update(venue_table=None)
        TableSection.objects.filter(restaurant=restaurant).update(venue_section=None)
        member.delete()
        if not venue.memberships.exists():
            venue.is_active = False
            venue.save(update_fields=["is_active", "updated_at"])
            venue.tables.update(is_active=False)
    return venue
