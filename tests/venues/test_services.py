from django.utils import timezone

import pytest

from apps.tables.models import Table, TableQRCode, TableSection, TableSession
from apps.venues import services
from apps.venues.models import Venue, VenueMember, VenueShareRequest, VenueTable

from .conftest import make_layout


def numbers(restaurant, shared=None):
    qs = Table.objects.filter(restaurant=restaurant)
    if shared is not None:
        qs = qs.filter(venue_table__isnull=not shared)
    return sorted(qs.values_list("number", flat=True))


@pytest.mark.django_db
class TestSendRequest:
    def test_creates_pending_and_emails_target_owner(self, a, b, a_manager, mailoutbox):
        req = services.send_share_request(a, b.slug, a_manager, venue_name="Hall", message="hi")
        assert req.status == "pending" and req.venue_name == "Hall"
        assert [m.to for m in mailoutbox] == [[b.owner.email]]
        assert "tenant-admin/venues/venuesharerequest" in mailoutbox[0].body

    @pytest.mark.parametrize("slug", ["no-such-place", "SELF"])
    def test_unknown_inactive_and_self_read_the_same(self, a, a_manager, slug):
        target = a.slug if slug == "SELF" else slug
        with pytest.raises(services.VenueError) as exc:
            services.send_share_request(a, target, a_manager)
        assert exc.value.message == "Restaurant not found."

    def test_inactive_target_reads_the_same(self, a, b, a_manager):
        b.is_active = False
        b.save(update_fields=["is_active"])
        with pytest.raises(services.VenueError, match="Restaurant not found"):
            services.send_share_request(a, b.slug, a_manager)

    def test_duplicate_pending_either_direction(self, a, b, a_manager, b_manager):
        services.send_share_request(a, b.slug, a_manager)
        with pytest.raises(services.VenueError, match="already pending"):
            services.send_share_request(a, b.slug, a_manager)
        with pytest.raises(services.VenueError, match="already pending"):
            services.send_share_request(b, a.slug, b_manager)

    def test_target_in_another_venue(self, venue_pair, rival, rival_manager, b):
        # rival (no venue) invites B (in venue): allowed, B's venue would be joined.
        services.send_share_request(rival, b.slug, rival_manager)
        # but two different venues can't merge
        other = Venue.objects.create(name="Other")
        VenueMember.objects.create(venue=other, restaurant=rival)
        with pytest.raises(services.VenueError, match="another venue"):
            services.send_share_request(rival, b.slug, rival_manager, message="second")


@pytest.mark.django_db
class TestAccept:
    def test_theirs_seeds_registry_from_sender_and_mirrors_into_acceptor(self, pending_request, a, b, b_manager):
        venue, summary = services.accept_share_request(pending_request.pk, b, b_manager, layout="theirs")

        assert venue.name == "Food Hall" and venue.slug == "food-hall"
        assert sorted(venue.tables.values_list("number", flat=True)) == ["1", "2", "3"]
        assert sorted(venue.sections.values_list("name", flat=True)) == ["Hall", "Terrace"]
        assert VenueMember.objects.get(restaurant=a).is_layout_seed is True
        assert VenueMember.objects.get(restaurant=b).display_order == 1

        # B: 1 and 3 created, its own 2 adopted (linked, not duplicated), 9 stays private.
        assert numbers(b, shared=True) == ["1", "2", "3"]
        assert numbers(b, shared=False) == ["9"]
        assert Table.objects.filter(restaurant=b).count() == 4
        for t in Table.objects.filter(restaurant=b, venue_table__isnull=False):
            assert t.qr_codes.filter(is_active=True).exists()
        assert TableSection.objects.get(restaurant=b, name="Terrace").venue_section is not None
        # A's own rows are linked too
        assert numbers(a, shared=True) == ["1", "2", "3"]
        assert summary.conflicts == [{"number": "2", "restaurant": b.slug, "resolution": "linked_existing"}]

        pending_request.refresh_from_db()
        assert pending_request.status == "accepted" and pending_request.responded_by == b_manager

    def test_ours_seeds_from_acceptor(self, pending_request, a, b, b_manager):
        venue, _ = services.accept_share_request(pending_request.pk, b, b_manager, layout="ours")
        assert sorted(venue.tables.values_list("number", flat=True)) == ["2", "9"]
        assert VenueMember.objects.get(restaurant=b).is_layout_seed is True
        assert numbers(a, shared=True) == ["2", "9"]
        assert numbers(a, shared=False) == ["1", "3"]

    def test_layout_required_on_first_accept(self, pending_request, b, b_manager):
        with pytest.raises(services.VenueError, match="whose table layout"):
            services.accept_share_request(pending_request.pk, b, b_manager, layout=None)

    def test_only_target_can_accept(self, pending_request, a, a_manager, rival, rival_manager):
        for restaurant, user in ((a, a_manager), (rival, rival_manager)):
            with pytest.raises(services.VenueError) as exc:
                services.accept_share_request(pending_request.pk, restaurant, user, layout="ours")
            assert exc.value.status_code == 404

    def test_second_accept_is_404_and_expired_is_410(self, pending_request, b, b_manager, a, a_manager):
        services.accept_share_request(pending_request.pk, b, b_manager, layout="theirs")
        with pytest.raises(services.VenueError) as exc:
            services.accept_share_request(pending_request.pk, b, b_manager, layout="theirs")
        assert exc.value.status_code == 404

        req = services.send_share_request(a, "rival-bistro", a_manager) if False else None  # placeholder for clarity
        old = VenueShareRequest.objects.create(
            from_restaurant=a, to_restaurant=b, expires_at=timezone.now() - timezone.timedelta(days=1)
        )
        with pytest.raises(services.VenueError) as exc:
            services.accept_share_request(old.pk, b, b_manager)
        assert exc.value.status_code == 410

    def test_third_restaurant_joins_existing_venue_and_inherits_layout(
        self, venue_pair, a, a_manager, rival, rival_manager
    ):
        req = services.send_share_request(a, rival.slug, a_manager)
        with pytest.raises(services.VenueError, match="already has a layout"):
            services.accept_share_request(req.pk, rival, rival_manager, layout="ours")
        venue, _ = services.accept_share_request(req.pk, rival, rival_manager)
        assert venue == venue_pair
        assert numbers(rival, shared=True) == ["1", "2", "3"]
        assert VenueMember.objects.get(restaurant=rival).display_order == 2

    def test_newcomer_inviting_a_member_joins_their_venue(self, venue_pair, b, rival, rival_manager, b_manager):
        req = services.send_share_request(rival, b.slug, rival_manager)
        venue, _ = services.accept_share_request(req.pk, b, b_manager)
        assert venue == venue_pair and numbers(rival, shared=True) == ["1", "2", "3"]

    def test_reverse_pending_request_is_cancelled_on_accept(self, a, b, a_manager, b_manager, a_layout, b_layout):
        forward = services.send_share_request(a, b.slug, a_manager)
        forward.status = "declined"
        forward.save()
        backward = services.send_share_request(b, a.slug, b_manager)
        forward2 = VenueShareRequest.objects.create(
            from_restaurant=a, to_restaurant=b, expires_at=timezone.now() + timezone.timedelta(days=1)
        )
        services.accept_share_request(backward.pk, a, a_manager, layout="ours")
        forward2.refresh_from_db()
        assert forward2.status == "cancelled"

    def test_one_venue_per_restaurant_is_a_db_constraint(self, venue_pair, a):
        other = Venue.objects.create(name="Other")
        from django.db import IntegrityError, transaction

        with pytest.raises(IntegrityError), transaction.atomic():
            VenueMember.objects.create(venue=other, restaurant=a)


@pytest.mark.django_db
class TestSync:
    def test_idempotent(self, venue_pair, b):
        before = list(Table.objects.filter(restaurant=b).values("pk", "number", "capacity", "venue_table_id"))
        summary = services.sync_member(venue_pair, b)
        assert (summary.created, summary.linked, summary.updated) == (0, 0, 0)
        assert list(Table.objects.filter(restaurant=b).values("pk", "number", "capacity", "venue_table_id")) == before

    def test_registry_edit_fans_out_but_never_touches_status(self, venue_pair, a, b):
        b_two = Table.objects.get(restaurant=b, number="2")
        b_two.status = "occupied"
        b_two.save()
        vt = venue_pair.tables.get(number="2")
        services.update_venue_table(vt, capacity=8, name="Window")
        for r in (a, b):
            t = Table.objects.get(restaurant=r, number="2")
            assert (t.capacity, t.name) == (8, "Window")
        assert Table.objects.get(restaurant=b, number="2").status == "occupied"

    def test_new_shared_table_appears_everywhere(self, venue_pair, a, b):
        vt, summary = services.create_venue_table(venue_pair, number="15", capacity=6)
        assert summary.created == 2
        for r in (a, b):
            t = Table.objects.get(restaurant=r, number="15")
            assert t.venue_table == vt and t.capacity == 6 and t.qr_codes.exists()

    def test_number_colliding_with_a_members_private_table_is_refused(self, venue_pair, b):
        with pytest.raises(services.VenueError, match="already has its own table 9") as exc:
            services.create_venue_table(venue_pair, number="9")
        assert exc.value.status_code == 409
        assert not venue_pair.tables.filter(number="9").exists()

    def test_deactivate_retires_mirrors_unless_session_live(self, venue_pair, a, b):
        vt = venue_pair.tables.get(number="1")
        TableSession.objects.create(table=Table.objects.get(restaurant=a, number="1"))
        skipped = services.deactivate_venue_table(vt)
        assert skipped == [a.slug]
        assert Table.objects.get(restaurant=a, number="1").is_active is True
        assert Table.objects.get(restaurant=b, number="1").is_active is False
        assert Table.objects.filter(venue_table=vt).count() == 2  # nothing deleted

    def test_section_rename_propagates(self, venue_pair, a, b):
        vs = venue_pair.sections.get(name="Hall")
        services.update_venue_section(vs, name="Main Hall")
        assert TableSection.objects.filter(venue_section=vs, name="Main Hall").count() == 2

    def test_reconcile_command_dry_run_then_apply(self, venue_pair, b):
        from io import StringIO

        from django.core.management import call_command

        Table.objects.filter(restaurant=b, number="3").delete()
        out = StringIO()
        call_command("reconcile_venues", stdout=out)
        assert "created=1" in out.getvalue() and "Dry run" in out.getvalue()
        assert not Table.objects.filter(restaurant=b, number="3").exists()
        call_command("reconcile_venues", "--apply", stdout=StringIO())
        assert Table.objects.filter(restaurant=b, number="3", venue_table__isnull=False).exists()


@pytest.mark.django_db
class TestLeave:
    def test_leaving_unlinks_but_keeps_tables_and_codes(self, venue_pair, a, b):
        codes = set(TableQRCode.objects.filter(table__restaurant=b).values_list("code", flat=True))
        services.leave_venue(b)
        assert not VenueMember.objects.filter(restaurant=b).exists()
        assert numbers(b) == ["1", "2", "3", "9"] and numbers(b, shared=True) == []
        assert set(TableQRCode.objects.filter(table__restaurant=b).values_list("code", flat=True)) == codes
        assert numbers(a, shared=True) == ["1", "2", "3"]  # A unaffected
        venue_pair.refresh_from_db()
        assert venue_pair.is_active is True

    def test_last_member_leaving_deactivates_the_venue(self, venue_pair, a, b):
        services.leave_venue(b)
        services.leave_venue(a)
        venue_pair.refresh_from_db()
        assert venue_pair.is_active is False
        assert not venue_pair.tables.filter(is_active=True).exists()

    def test_leave_when_not_a_member(self, rival):
        with pytest.raises(services.VenueError) as exc:
            services.leave_venue(rival)
        assert exc.value.status_code == 404
