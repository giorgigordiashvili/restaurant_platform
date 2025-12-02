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
