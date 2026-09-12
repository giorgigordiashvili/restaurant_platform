"""The tenant admin speaks Georgian, English and Russian: catalogs load and cover the registry strings."""

from pathlib import Path

from django.conf import settings
from django.test import Client, override_settings
from django.utils import translation
from django.utils.translation import gettext

import pytest

LOCALE = [Path(settings.BASE_DIR) / "locale"]


@override_settings(LOCALE_PATHS=LOCALE)
def test_catalogs_cover_admin_strings():
    from apps.core import modules

    samples = ["Cash & payments", "Pause orders", "Warehouse overview", "Net sales", "Manager", "Reservations"]
    for lang in ("ka", "ru"):
        with translation.override(lang):
            for s in samples:
                assert gettext(s) != s, f"{s!r} untranslated in {lang}"
            for m in modules.MODULES:
                assert str(m.title)  # lazy titles resolve in every language
    with translation.override("en"):
        assert gettext("Cash & payments") == "Cash & payments"


@pytest.mark.django_db
@override_settings(LOCALE_PATHS=LOCALE)
def test_admin_follows_user_language_and_cookie(user, restaurant, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    c = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    c.force_login(user)
    # preferred_language defaults to Georgian -> the dashboard renders in Georgian
    page = c.get("/tenant-admin/")
    assert page.status_code == 200 and 'lang="ka"' in page.content.decode()
    assert "სალარო და გადახდები" in page.content.decode() or "მენიუ" in page.content.decode()
    # the switcher sets the language cookie, which wins over the profile
    c.cookies[settings.LANGUAGE_COOKIE_NAME] = "ru"
    page = c.get("/tenant-admin/")
    assert 'lang="ru"' in page.content.decode()
    c.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    page = c.get("/tenant-admin/")
    assert 'lang="en"' in page.content.decode() and "Modules" in page.content.decode()
