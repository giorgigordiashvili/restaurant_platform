"""
Tests for tables models.
"""

import pytest


@pytest.mark.django_db
class TestTableSectionModel:
    """Tests for TableSection model."""

    def test_create_section(self, create_table_section, restaurant):
        """Test creating a table section."""
        section = create_table_section(restaurant=restaurant, name="Patio")
        assert section.name == "Patio"
        assert section.restaurant == restaurant
        assert section.is_active is True

    def test_section_str(self, table_section):
        """Test section string representation."""
        assert "Main Hall" in str(table_section)

    def test_section_ordering(self, create_table_section, restaurant):
        """Test sections are ordered by display_order and name."""
        s1 = create_table_section(restaurant=restaurant, name="B Section", display_order=2)
        s2 = create_table_section(restaurant=restaurant, name="A Section", display_order=1)
        s3 = create_table_section(restaurant=restaurant, name="C Section", display_order=1)

        from apps.tables.models import TableSection

        sections = list(TableSection.objects.filter(restaurant=restaurant))
        # Ordered by display_order first, then name
        assert sections[0] == s2  # display_order=1, name=A
        assert sections[1] == s3  # display_order=1, name=C
        assert sections[2] == s1  # display_order=2


@pytest.mark.django_db
class TestTableModel:
    """Tests for Table model."""

    def test_create_table(self, create_table, restaurant):
        """Test creating a table."""
        table = create_table(restaurant=restaurant, number="VIP-1", capacity=6)
        assert table.number == "VIP-1"
        assert table.capacity == 6
        assert table.status == "available"
        assert table.is_active is True

    def test_table_str(self, table):
        """Test table string representation."""
        assert "T1" in str(table)

    def test_set_occupied(self, table):
        """Test setting table as occupied."""
        table.set_occupied()
        assert table.status == "occupied"

    def test_set_available(self, table):
        """Test setting table as available."""
        table.status = "occupied"
        table.save()
        table.set_available()
        assert table.status == "available"

    def test_unique_number_per_restaurant(self, create_table, restaurant):
        """Test that table numbers must be unique per restaurant."""
        create_table(restaurant=restaurant, number="A1")
        with pytest.raises(Exception):  # IntegrityError
            create_table(restaurant=restaurant, number="A1")


@pytest.mark.django_db
class TestTableQRCodeModel:
    """Tests for TableQRCode model."""

    def test_create_qr_code(self, create_qr_code, table):
        """Test creating a QR code."""
        qr = create_qr_code(table=table, code="mycode123")
        assert qr.code == "mycode123"
        assert qr.table == table
        assert qr.is_active is True

    def test_generate_code(self, table):
        """Test auto-generating QR code."""
        from apps.tables.models import TableQRCode

        qr = TableQRCode.objects.create(table=table)
        assert qr.code is not None
        assert len(qr.code) > 0

    def test_record_scan(self, table_qr_code):
        """Test recording a QR scan."""
        initial_count = table_qr_code.scans_count
        table_qr_code.record_scan()
        assert table_qr_code.scans_count == initial_count + 1
        assert table_qr_code.last_scanned_at is not None

    def test_get_table_by_code(self, table_qr_code):
        """Test getting table by QR code."""
        from apps.tables.models import TableQRCode

        found_table = TableQRCode.get_table_by_code(table_qr_code.code)
        assert found_table == table_qr_code.table

    def test_get_table_by_invalid_code(self):
        """Test getting table by invalid QR code returns None."""
        from apps.tables.models import TableQRCode

        found_table = TableQRCode.get_table_by_code("invalidcode")
        assert found_table is None


@pytest.mark.django_db
class TestTableSessionModel:
    """Tests for TableSession model."""

    def test_create_session(self, create_table_session, table):
        """Test creating a table session."""
        session = create_table_session(table=table, guest_count=4)
        assert session.guest_count == 4
        assert session.status == "active"

    def test_close_session(self, table_session):
        """Test closing a table session."""
        table_session.close()
        assert table_session.status == "closed"
        assert table_session.closed_at is not None

    def test_close_session_sets_table_available(self, table_session, table):
        """Test that closing session sets table to available."""
        table.status = "occupied"
        table.save()

        table_session.close()
        table.refresh_from_db()
        assert table.status == "available"

    def test_session_duration(self, table_session):
        """Test session duration calculation."""
        duration = table_session.duration
        assert duration is not None
        assert duration.total_seconds() >= 0

    def test_session_active_check(self, table_session):
        """Test is_active property."""
        assert table_session.is_active is True
        table_session.close()
        assert table_session.is_active is False

    def test_invite_code_generated_on_create(self, table):
        """Test that invite code is auto-generated on session creation."""
        from apps.tables.models import TableSession

        session = TableSession.objects.create(table=table, guest_count=2)
        assert session.invite_code is not None
        assert len(session.invite_code) == 8

    def test_invite_code_unique(self, create_table, restaurant):
        """Test that invite codes are unique."""
        from apps.tables.models import TableSession

        table1 = create_table(restaurant=restaurant, number="T100")
        table2 = create_table(restaurant=restaurant, number="T101")
        session1 = TableSession.objects.create(table=table1)
        session2 = TableSession.objects.create(table=table2)
        assert session1.invite_code != session2.invite_code

    def test_get_or_create_guest_authenticated(self, table_session_with_host, another_user):
        """Test getting or creating a guest for authenticated user."""
        guest, created = table_session_with_host.get_or_create_guest(user=another_user)
        assert created is True
        assert guest.user == another_user
        assert guest.is_host is False

        # Second call should return existing
        guest2, created2 = table_session_with_host.get_or_create_guest(user=another_user)
        assert created2 is False
        assert guest2.id == guest.id

    def test_get_or_create_guest_anonymous(self, table_session_with_host):
        """Test creating a guest for anonymous user."""
        guest, created = table_session_with_host.get_or_create_guest(guest_name="John")
        assert created is True
        assert guest.user is None
        assert guest.guest_name == "John"


@pytest.mark.django_db
class TestTableSessionGuestModel:
    """Tests for TableSessionGuest model."""

    def test_create_guest(self, create_session_guest, table_session, user):
        """Test creating a session guest."""
        guest = create_session_guest(session=table_session, user=user, is_host=True)
        assert guest.user == user
        assert guest.is_host is True
        assert guest.status == "active"

    def test_guest_display_name_authenticated(self, create_session_guest, table_session, user):
        """Test display name for authenticated guest."""
        guest = create_session_guest(session=table_session, user=user)
        assert guest.display_name == user.email

    def test_guest_display_name_anonymous(self, create_session_guest, table_session):
        """Test display name for anonymous guest."""
        guest = create_session_guest(session=table_session, guest_name="John Doe")
        assert guest.display_name == "John Doe"

    def test_guest_display_name_fallback(self, create_session_guest, table_session):
        """Test display name fallback for anonymous guest without name."""
        guest = create_session_guest(session=table_session, guest_name="")
        assert guest.display_name == "Guest"

    def test_guest_leave(self, create_session_guest, table_session):
        """Test guest leaving session."""
        guest = create_session_guest(session=table_session, guest_name="Leaving Guest")
        guest.leave()
        assert guest.status == "left"
        assert guest.left_at is not None

    def test_guest_str(self, create_session_guest, table_session, user):
        """Test guest string representation."""
        guest = create_session_guest(session=table_session, user=user)
        assert user.email in str(guest)

    def test_unique_user_per_session(self, create_session_guest, table_session, user):
        """Test that a user can only be a guest once per session."""
        create_session_guest(session=table_session, user=user)
        with pytest.raises(Exception):  # IntegrityError
            create_session_guest(session=table_session, user=user)
