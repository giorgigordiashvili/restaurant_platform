"""First-login setup wizard: routing, steps, module decisions."""

from django.test import Client

import pytest

from apps.core import modules
from apps.tenants import setup
from apps.tenants.models import Restaurant, RestaurantHours

URL = "/tenant-admin/tenants/restaurantsetup/"


@pytest.fixture
def fresh(restaurant):
    restaurant.setup_completed_at = None
    restaurant.setup_state = {}
    restaurant.save(update_fields=["setup_completed_at", "setup_state"])
    RestaurantHours.create_default_hours(restaurant)
    return restaurant


@pytest.fixture
def owner_admin(user, fresh, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=fresh, role=next(r for r in staff_roles if r.name == "owner"))
    client = Client(HTTP_HOST=f"{fresh.slug}.localhost")
    client.force_login(user)
    return client


@pytest.fixture
def waiter_admin(waiter_user, waiter_staff, fresh):
    client = Client(HTTP_HOST=f"{fresh.slug}.localhost")
    client.force_login(waiter_user)
    return client


@pytest.mark.django_db
class TestRouting:
    def test_owner_is_sent_to_wizard_until_finished(self, owner_admin, fresh):
        r = owner_admin.get("/tenant-admin/")
        assert r.status_code == 302 and r["Location"].endswith(URL)
        setup.finish(fresh)
        assert owner_admin.get("/tenant-admin/").status_code == 200

    def test_staff_without_settings_rights_see_the_dashboard(self, waiter_admin):
        assert waiter_admin.get("/tenant-admin/").status_code == 200

    def test_finish_later_stops_redirect_and_shows_card(self, owner_admin, fresh):
        assert owner_admin.post(URL + "later/").status_code == 302
        fresh.refresh_from_db()
        assert fresh.setup_completed_at is not None and setup.is_deferred(fresh)
        html = owner_admin.get("/tenant-admin/").content.decode()
        assert 'data-testid="card-setup"' in html

    def test_existing_restaurants_are_marked_complete_by_migration(self, restaurant):
        # conftest restaurants are created after the migration, so emulate its rule:
        Restaurant.objects.filter(pk=restaurant.pk).update(setup_completed_at=None)
        from importlib import import_module

        from apps.tenants.migrations import __name__ as _pkg  # noqa: F401 -- import guard

        mig = import_module("apps.tenants.migrations.0029_setup_wizard")
        from django.apps import apps as django_apps

        mig.mark_existing_complete(django_apps, None)
        restaurant.refresh_from_db()
        assert restaurant.setup_completed_at is not None


@pytest.mark.django_db
class TestSteps:
    def test_welcome_lists_switchable_modules_grouped(self, owner_admin):
        html = owner_admin.get(URL).content.decode()
        assert 'data-step="welcome"' in html
        for code in ("ordering", "reservations", "gift_cards", "timekeeping"):
            assert f'value="{code}"' in html
        assert 'value="menu"' not in html  # always on, never asked

    def test_selection_adds_requirements_and_switches_off_unticked(self, owner_admin, fresh):
        fresh.reviews_enabled = True
        fresh.save(update_fields=["reviews_enabled"])
        r = owner_admin.post(URL + "select/", {"modules": ["gift_cards", "tables"]})
        assert r.status_code == 302 and "step=details" in r["Location"]
        fresh.refresh_from_db()
        assert setup.selected_codes(fresh) == ["ordering", "tables", "cash", "gift_cards"]
        assert not fresh.reviews_enabled
        keys = [s.key for s in setup.steps(fresh)]
        assert keys == [
            "welcome",
            "details",
            "branding",
            "hours",
            "module:ordering",
            "module:tables",
            "module:cash",
            "module:gift_cards",
            "done",
        ]

    def test_details_saves_and_advances(self, owner_admin, fresh):
        r = owner_admin.post(
            URL + "details/",
            {
                "name": "ღვინის ბარი",
                "description": "",
                "phone": "+995555000000",
                "email": "",
                "website": "",
                "address": "Rustaveli 1",
                "city": "Tbilisi",
                "country": "Georgia",
                "timezone": "Asia/Tbilisi",
                "default_currency": "GEL",
                "default_language": "ka",
            },
        )
        assert r.status_code == 302 and "step=branding" in r["Location"]
        fresh.refresh_from_db()
        assert fresh.name == "ღვინის ბარი" and fresh.city == "Tbilisi"

    def test_details_validation_error_rerenders(self, owner_admin, fresh):
        r = owner_admin.post(
            URL + "details/",
            {
                "name": "",
                "timezone": "Asia/Tbilisi",
                "default_currency": "GEL",
                "default_language": "ka",
                "country": "Georgia",
            },
        )
        assert r.status_code == 400 and 'data-step="details"' in r.content.decode()

    def test_branding_skip_and_hours(self, owner_admin, fresh):
        r = owner_admin.post(URL + "branding/", {"skip": "1"})
        assert "step=hours" in r["Location"]
        data = {"closed_6": "1"}
        for d in range(7):
            data[f"open_{d}"] = "10:00"
            data[f"close_{d}"] = "23:30"
        data["open2_0"] = "12:00"
        data["close2_0"] = "15:00"
        r = owner_admin.post(URL + "hours/", data)
        assert r.status_code == 302 and "step=module:" in r["Location"]
        rows = {h.day_of_week: h for h in fresh.operating_hours.all()}
        assert rows[6].is_closed and str(rows[1].close_time) == "23:30:00"
        assert str(rows[0].open_time_2) == "12:00:00" and rows[1].open_time_2 is None

    def test_module_enable_turns_on_requirements_and_redirects_to_landing(self, owner_admin, fresh):
        fresh.cash_enabled = False
        fresh.save(update_fields=["cash_enabled"])
        owner_admin.post(URL + "select/", {"modules": ["gift_cards"]})
        fresh.refresh_from_db()
        assert not fresh.cash_enabled and not fresh.gift_cards_enabled
        html = owner_admin.get(URL + "?step=module:gift_cards").content.decode()
        assert 'data-testid="setup-module-gift_cards"' in html
        r = owner_admin.post(URL + "module/gift_cards/enable/")
        assert r.status_code == 302 and r["Location"].endswith("/tenant-admin/giftcards/giftcard/")
        fresh.refresh_from_db()
        assert fresh.cash_enabled and fresh.gift_cards_enabled and fresh.accepts_remote_orders
        assert setup.state(fresh)["decisions"]["gift_cards"] == "enabled"
        assert setup.state(fresh)["current"] == "done"
        # The landing page carries a "continue the setup" message.
        page = owner_admin.get("/tenant-admin/giftcards/giftcard/").content.decode()
        assert "continue the setup" in page

    def test_module_enable_stay_and_options(self, owner_admin, fresh):
        owner_admin.post(URL + "select/", {"modules": ["ordering"]})
        r = owner_admin.post(URL + "module/ordering/enable/", {"stay": "1"})
        assert r.status_code == 302 and "step=done" in r["Location"]
        fresh.refresh_from_db()
        assert fresh.accepts_remote_orders and not fresh.accepts_takeaway

    def test_module_skip_switches_off(self, owner_admin, fresh):
        fresh.accepts_reservations = True
        fresh.save(update_fields=["accepts_reservations"])
        owner_admin.post(URL + "select/", {"modules": ["reservations"]})
        r = owner_admin.post(URL + "module/reservations/skip/")
        assert r.status_code == 302 and "step=done" in r["Location"]
        fresh.refresh_from_db()
        assert not fresh.accepts_reservations
        assert setup.state(fresh)["decisions"]["reservations"] == "skipped"

    def test_kitchen_has_no_landing_and_finish(self, owner_admin, fresh):
        owner_admin.post(URL + "select/", {"modules": ["kitchen"]})
        html = owner_admin.get(URL + "?step=module:kitchen").content.decode()
        assert "Turn on, set up later" not in html and "turn on" in html.lower()
        r = owner_admin.post(URL + "module/kitchen/enable/")
        assert "step=done" in r["Location"]
        html = owner_admin.get(URL + "?step=done").content.decode()
        assert 'data-testid="setup-done"' in html and "Kitchen display" in html
        r = owner_admin.post(URL + "finish/")
        assert r.status_code == 302 and r["Location"].endswith("/tenant-admin/")
        fresh.refresh_from_db()
        assert fresh.setup_completed_at is not None and setup.state(fresh)["finished_at"]
        assert owner_admin.get("/tenant-admin/").status_code == 200

    def test_restart(self, owner_admin, fresh):
        setup.finish(fresh)
        r = owner_admin.post(URL + "restart/")
        assert "step=welcome" in r["Location"]
        fresh.refresh_from_db()
        assert fresh.setup_completed_at is None and fresh.setup_state == {}

    def test_waiter_cannot_post(self, waiter_admin):
        assert waiter_admin.post(URL + "select/", {"modules": ["ordering"]}).status_code == 403

    def test_sidebar_has_wizard_entry(self, owner_admin, fresh):
        setup.finish(fresh)
        html = owner_admin.get("/tenant-admin/").content.decode()
        assert "Setup wizard" in html


@pytest.mark.django_db
class TestSlugs:
    def test_georgian_name_transliterated(self, user):
        r = Restaurant.objects.create(name="ღვინის ბარი", owner=user)
        assert r.slug == "ghvinis-bari"

    def test_cyrillic_and_fallback(self, user):
        assert Restaurant.objects.create(name="Кафе Пушкин", owner=user).slug == "kafe-pushkin"
        assert Restaurant.objects.create(name="!!!", owner=user).slug == "restaurant"
        assert Restaurant.objects.create(name="???", owner=user).slug == "restaurant-1"

    def test_module_landing_urls_resolve(self):
        for m in modules.MODULES:
            if m.switchable and m.code != "kitchen":
                assert setup.landing_url(m.code), m.code
        assert setup.landing_url("kitchen") is None
