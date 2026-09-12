from io import StringIO

from django.core.management import call_command

import pytest

from apps.tables.models import TableQRCode


@pytest.mark.django_db
def test_regenerate_qr_images_dry_run_then_apply(table_qr_code, venue_pair):
    TableQRCode.objects.filter(pk=table_qr_code.pk).update(qr_image_url="legacy")
    out = StringIO()
    call_command("regenerate_qr_images", stdout=out)
    assert "1 to regenerate" in out.getvalue() and "Dry run" in out.getvalue()
    table_qr_code.refresh_from_db()
    assert not table_qr_code.image_is_current

    call_command("regenerate_qr_images", "--apply", stdout=StringIO())
    table_qr_code.refresh_from_db()
    assert table_qr_code.image_is_current

    out = StringIO()
    call_command("regenerate_qr_images", "--restaurant", "no-such-slug", stdout=out)
    assert "table codes: 0 total" in out.getvalue()
