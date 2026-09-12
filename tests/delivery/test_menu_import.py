"""Menu import from Wolt: normaliser, preview, apply (categories / dishes / options / photos), admin and API."""

from decimal import Decimal

from django.test import Client

import pytest

from apps.delivery import menu_import
from apps.delivery.models import MenuImport
from apps.menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, ModifierGroup
from tests.delivery.test_wolt import TOKEN

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class TestNormalize:
    def test_wolt_v2_shape(self, wolt_fixture):
        menu = menu_import.normalize_wolt_menu(wolt_fixture("menu_v2"))
        assert menu.price_units == "minor" and menu.currency == "GEL" and menu.item_count == 3
        hot = menu.categories[0]
        assert hot.names == {"ka": "ცხელი კერძები", "en": "Hot dishes"}
        khinkali = hot.items[0]
        assert khinkali.price == Decimal("12.50") and khinkali.image_url.endswith("khinkali.jpg")
        assert khinkali.descriptions == {"en": "Juicy dumplings"} and khinkali.enabled
        assert hot.items[1].enabled is False
        opt = khinkali.options[0]
        assert opt.multiple and opt.min == 0 and opt.max == 2 and opt.names["en"] == "Extras"
        assert [(v.names["en"], v.price, v.default) for v in opt.values] == [
            ("Cheese", Decimal("1.50"), False),
            ("Butter", Decimal("0.00"), True),
        ]
        forced = menu_import.normalize_wolt_menu(wolt_fixture("menu_v2"), price_units="major")
        assert forced.categories[0].items[0].price == Decimal("1250.00")

    def test_units_and_tolerance(self):
        assert menu_import.detect_price_units([Decimal("12.5"), Decimal("1250")]) == "major"
        assert menu_import.detect_price_units([Decimal("1250"), Decimal("900")]) == "minor"
        assert menu_import.detect_price_units([Decimal("12"), Decimal("9")]) == "major"
        assert menu_import.detect_price_units([]) == "major"
        # upload shape (items inline, options inline) and garbage are tolerated
        inline = {
            "categories": [
                {"name": [{"lang": "en", "value": "A"}], "items": [{"name": "Soup", "price": 4.5, "options": []}]}
            ]
        }
        menu = menu_import.normalize_wolt_menu(inline)
        assert menu.categories[0].items[0].names == {"": "Soup"} and menu.categories[0].items[0].price == Decimal(
            "4.50"
        )
        assert menu_import.normalize_wolt_menu({}).categories == []
        orphan = {"items": [{"id": "x", "name": [{"lang": "en", "value": "Lonely"}], "price": 3}]}
        assert menu_import.normalize_wolt_menu(orphan).categories[0].names["en"] == "Other"


@pytest.mark.django_db
class TestPreviewAndApply:
    def test_preview_matches_existing_by_name(self, wolt_link, wolt_fixture, menu_item):
        menu = menu_import.normalize_wolt_menu(wolt_fixture("menu_v2"))
        preview = menu_import.build_preview(wolt_link.restaurant, menu)
        assert preview["counts"] == {"categories": 2, "items": 3, "new": 2, "existing": 1}
        rows = {i["name"]: i for c in preview["categories"] for i in c["items"]}
        assert rows["Test Dish"]["existing"] and rows["Test Dish"]["existing_price"] == str(menu_item.price)
        assert (
            rows["ხინკალი"]["options"] == 1
            and rows["ხინკალი"]["image"]
            and rows["ხინკალი"]["languages"] == ["en", "ka"]
        )

    def test_fetch_and_apply(
        self,
        wolt_link,
        wolt_fixture,
        menu_item,
        menu_category,
        fake_wolt,
        user,
        django_capture_on_commit_callbacks,
        settings,
        tmp_path,
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        fake_wolt.queue.extend([TOKEN, (200, wolt_fixture("menu_v2"))])
        row = menu_import.fetch_preview(wolt_link, by=user)
        assert row.status == "previewed" and row.preview["counts"]["new"] == 2
        assert fake_wolt.calls[-1]["url"].endswith("/v2/venues/venue-1/menu")
        fake_wolt.queue.extend([(200, PNG), (200, PNG)])
        with django_capture_on_commit_callbacks(execute=True):
            stats = menu_import.apply(row, by=user, download_images=True)
        assert stats == {
            "categories_created": 2,
            "items_created": 2,
            "items_updated": 0,
            "items_skipped": 1,
            "groups_created": 1,
            "images_queued": 2,
        }
        restaurant = wolt_link.restaurant
        hot = MenuCategory.objects.get(restaurant=restaurant, translations__name="Hot dishes")
        assert hot.safe_translation_getter("name", language_code="ka") == "ცხელი კერძები"
        khinkali = MenuItem.objects.get(restaurant=restaurant, translations__name="Khinkali")
        assert khinkali.category == hot and khinkali.price == Decimal("12.50") and khinkali.is_available
        assert khinkali.safe_translation_getter("name", language_code="ka") == "ხინკალი"
        assert khinkali.safe_translation_getter("description", language_code="en") == "Juicy dumplings"
        ojakhuri = MenuItem.objects.get(restaurant=restaurant, translations__name="Ojakhuri")
        assert ojakhuri.is_available is False and ojakhuri.price == Decimal("24.00")
        group = ModifierGroup.objects.get(restaurant=restaurant, translations__name="Extras")
        assert group.selection_type == "multiple" and group.max_selections == 2 and group.internal_name == "wolt:opt-1"
        mods = {m.safe_translation_getter("name", any_language=True): m for m in group.modifiers.all()}
        assert mods["Cheese"].price_adjustment == Decimal("1.50") and mods["Butter"].is_default
        assert MenuItemModifierGroup.objects.filter(menu_item=khinkali, modifier_group=group).exists()
        # the existing "Test Dish" was matched and left alone (no duplicate, price unchanged)
        assert MenuItem.objects.filter(restaurant=restaurant, translations__name="Test Dish").count() == 1
        row.refresh_from_db()
        assert row.status == "imported" and row.stats["images_done"] == 2
        khinkali.refresh_from_db()
        assert khinkali.image and "khinkali" in khinkali.image.name
        # a second run creates nothing new and can update the price of what exists
        fake_wolt.queue.append((200, wolt_fixture("menu_v2")))
        row2 = menu_import.fetch_preview(wolt_link, by=user)
        assert row2.preview["counts"] == {"categories": 2, "items": 3, "new": 0, "existing": 3}
        menu_item.price = Decimal("1.00")
        menu_item.save()
        with django_capture_on_commit_callbacks(execute=True):
            stats = menu_import.apply(row2, by=user, update_prices=True, download_images=False)
        assert stats["items_created"] == 0 and stats["items_updated"] == 1 and stats["categories_created"] == 0
        menu_item.refresh_from_db()
        assert menu_item.price == Decimal("9.00")
        assert MenuItem.objects.filter(restaurant=restaurant).count() == 3
        with pytest.raises(menu_import.ImportError_):
            menu_import.apply(row2, by=user)  # already imported

    def test_fetch_errors(self, wolt_link, glovo_link, fake_wolt, user):
        with pytest.raises(menu_import.ImportError_) as exc:
            menu_import.fetch_preview(glovo_link, by=user)
        assert exc.value.code == "not_supported"
        fake_wolt.queue.extend([TOKEN, (503, {})])
        with pytest.raises(menu_import.ImportError_) as exc:
            menu_import.fetch_preview(wolt_link, by=user)
        assert exc.value.code == "platform_error" and MenuImport.objects.get().status == "failed"
        fake_wolt.queue.append((200, {"menu": {"categories": []}}))
        with pytest.raises(menu_import.ImportError_) as exc:
            menu_import.fetch_preview(wolt_link, by=user)
        assert exc.value.code == "empty_menu"

    def test_bad_image_is_skipped(
        self, wolt_link, wolt_fixture, fake_wolt, user, django_capture_on_commit_callbacks, settings, tmp_path
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        fake_wolt.queue.extend([TOKEN, (200, wolt_fixture("menu_v2"))])
        row = menu_import.fetch_preview(wolt_link, by=user)
        fake_wolt.queue.extend([(404, {}), (200, PNG)])
        with django_capture_on_commit_callbacks(execute=True):
            menu_import.apply(row, by=user)
        row.refresh_from_db()
        assert row.stats["images_done"] == 1 and row.stats["images_failed"] == 1 and row.status == "imported"


@pytest.mark.django_db
class TestAdminAndApi:
    def test_admin_flow(
        self, user, delivery_restaurant, staff_roles, create_staff_member, wolt_link, fake_wolt, wolt_fixture
    ):
        create_staff_member(
            user=user, restaurant=delivery_restaurant, role=next(r for r in staff_roles if r.name == "owner")
        )
        c = Client(HTTP_HOST=f"{delivery_restaurant.slug}.localhost")
        c.force_login(user)
        base = "/tenant-admin/delivery/deliveryplatformspage/"
        html = c.get(base).content.decode()
        assert 'data-testid="import-menu"' in html and 'data-testid="guide-link"' in html
        guide = c.get(f"{base}guide/")
        assert guide.status_code == 200 and "/api/v1/delivery/wolt/orders/" in guide.content.decode()
        assert "Video guide coming soon" in guide.content.decode()
        fake_wolt.queue.extend([TOKEN, (200, wolt_fixture("menu_v2"))])
        res = c.post(f"{base}wolt/import-menu/", {"price_units": "auto"})
        assert res.status_code == 302 and "/imports/" in res.url
        page = c.get(res.url)
        body = page.content.decode()
        assert page.status_code == 200 and "ხინკალი" in body and 'data-testid="import-apply"' in body
        apply_url = res.url + "apply/"
        res = c.post(apply_url, {"update_prices": "", "download_images": ""})
        assert res.status_code == 302 and res.url.endswith("/tenant-admin/menu/menuitem/")
        assert MenuItem.objects.filter(restaurant=delivery_restaurant, translations__name="Khinkali").exists()
        assert MenuImport.objects.get().status == "imported"
        assert c.post(f"{base}glovo/import-menu/").status_code == 302  # error message, back to the page
        assert MenuImport.objects.count() == 1

    def test_api_flow(
        self,
        authenticated_owner_client,
        user,
        delivery_restaurant,
        staff_roles,
        create_staff_member,
        wolt_link,
        fake_wolt,
        wolt_fixture,
    ):
        create_staff_member(
            user=user, restaurant=delivery_restaurant, role=next(r for r in staff_roles if r.name == "owner")
        )
        api = authenticated_owner_client
        api.defaults["HTTP_X_RESTAURANT"] = delivery_restaurant.slug
        fake_wolt.queue.extend([TOKEN, (200, wolt_fixture("menu_v2"))])
        res = api.post("/api/v1/dashboard/delivery/platforms/wolt/import-menu/", {"price_units": "auto"}, format="json")
        assert res.status_code == 200, res.content
        data = res.json()
        assert data["status"] == "previewed" and data["preview"]["counts"]["new"] == 3
        res = api.get(f"/api/v1/dashboard/delivery/imports/{data['id']}/")
        assert res.status_code == 200 and res.json()["id"] == data["id"]
        res = api.post(
            f"/api/v1/dashboard/delivery/imports/{data['id']}/apply/", {"download_images": False}, format="json"
        )
        assert res.status_code == 200 and res.json()["stats"]["items_created"] == 3
        assert res.json()["status"] == "imported"
        res = api.post("/api/v1/dashboard/delivery/platforms/glovo/import-menu/", {}, format="json")
        assert res.status_code == 400 and res.json()["error"]["code"] == "not_supported"
