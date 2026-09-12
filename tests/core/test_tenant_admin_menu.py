"""
Restaurant staff enter menu content through the tenant admin at
<slug>.admin.aimenu.ge/tenant-admin/. These tests pin down three things that
were silently broken there:

- every translatable menu form offers all three languages (the tabs vanished
  when SITE_ID was introduced, leaving only Georgian editable);
- a tenant never sees, and cannot pick, another restaurant's rows;
- saving works in each language, including the Modifier Group form, which had
  no name field and crashed with a NULL restaurant.
"""

import re

import pytest

from apps.menu.models import MenuCategory, MenuItem, Modifier, ModifierGroup

LANGS = ("ka", "en", "ru")

# Inline management-form fields the ModifierGroup form always posts.
NO_INLINE_MODIFIERS = {
    "modifiers-TOTAL_FORMS": "0",
    "modifiers-INITIAL_FORMS": "0",
    "modifiers-MIN_NUM_FORMS": "0",
    "modifiers-MAX_NUM_FORMS": "1000",
}


@pytest.fixture(autouse=True)
def _tenant_domain(settings):
    settings.MAIN_DOMAIN = "localhost"


@pytest.fixture
def manager_client(client, manager_staff, restaurant):
    """A logged-in restaurant manager, on the restaurant's own admin subdomain."""
    client.force_login(manager_staff.user)
    client.defaults["HTTP_HOST"] = f"{restaurant.slug}.localhost"
    return client


@pytest.fixture
def rival(create_restaurant, create_user):
    """Another tenant whose data must never leak into this restaurant's forms."""
    owner = create_user(email="rival@example.com", first_name="Rival", last_name="Owner")
    return create_restaurant(owner=owner, name="Rival Bistro", slug="rival-bistro")


def _translated(restaurant, model, **fields):
    """Create a translatable row with only a Georgian translation."""
    obj = model(restaurant=restaurant, **fields)
    obj.set_current_language("ka")
    obj.name = "ხინკალი"
    obj.save()
    return obj


def _tab_html(html, code):
    match = re.search(rf'<(a|span)[^>]*data-language="{code}"[^>]*>(.*?)</\1>', html, re.S)
    assert match, f"no language tab for {code}"
    return match.group(2)


@pytest.mark.django_db
class TestLanguageTabs:
    @pytest.mark.parametrize("url", ["menu/menuitem/add/", "menu/menucategory/add/", "menu/modifiergroup/add/"])
    def test_every_menu_form_offers_all_languages(self, manager_client, url):
        html = manager_client.get(f"/tenant-admin/{url}").content.decode()

        assert 'data-testid="language-tabs"' in html
        for code in LANGS:
            assert f'data-language="{code}"' in html
        # The non-current tabs are links that switch the edited translation.
        assert "?language=en" in html and "?language=ru" in html

    def test_tab_switches_which_translation_is_edited(self, manager_client, restaurant):
        item = _translated(restaurant, MenuItem, price="9.50")
        url = f"/tenant-admin/menu/menuitem/{item.pk}/change/"

        ka = manager_client.get(url, {"language": "ka"}).content.decode()
        en = manager_client.get(url, {"language": "en"}).content.decode()

        assert 'value="ხინკალი"' in ka
        assert 'value="ხინკალი"' not in en  # English is still empty, not a copy of Georgian

    def test_tabs_show_which_languages_are_filled(self, manager_client, restaurant):
        item = _translated(restaurant, MenuItem, price="9.50")
        html = manager_client.get(f"/tenant-admin/menu/menuitem/{item.pk}/change/").content.decode()

        assert "check_circle" in _tab_html(html, "ka")
        assert "radio_button_unchecked" in _tab_html(html, "en")

    def test_changelist_shows_language_coverage(self, manager_client, restaurant):
        _translated(restaurant, MenuItem, price="9.50")
        html = manager_client.get("/tenant-admin/menu/menuitem/").content.decode()
        assert "language-buttons" in html


@pytest.mark.django_db
def test_platform_admin_site_gets_the_tabs_too(admin_client):
    # The superadmin site at admin.aimenu.ge/admin/ shares the settings fix.
    html = admin_client.get("/admin/menu/menuitem/add/").content.decode()
    assert 'data-testid="language-tabs"' in html
    for code in LANGS:
        assert f'data-language="{code}"' in html


@pytest.mark.django_db
class TestSavingTranslations:
    def test_each_language_is_saved_without_touching_the_others(self, manager_client, restaurant):
        category = _translated(restaurant, MenuCategory)
        url = f"/tenant-admin/menu/menucategory/{category.pk}/change/"
        base = {"description": "", "display_order": "1", "is_active": "on"}

        for code, name in (("en", "Khinkali"), ("ru", "Хинкали")):
            resp = manager_client.post(f"{url}?language={code}", {**base, "name": name})
            assert resp.status_code == 302, resp.content[:500]

        category = MenuCategory.objects.get(pk=category.pk)
        assert category.safe_translation_getter("name", language_code="ka") == "ხინკალი"
        assert category.safe_translation_getter("name", language_code="en") == "Khinkali"
        assert category.safe_translation_getter("name", language_code="ru") == "Хинкали"

    def test_new_object_created_in_a_non_default_language(self, manager_client, restaurant):
        resp = manager_client.post(
            "/tenant-admin/menu/menucategory/add/?language=en",
            {"name": "Soups", "description": "", "display_order": "1", "is_active": "on"},
        )
        assert resp.status_code == 302, resp.content[:500]

        category = MenuCategory.objects.get(restaurant=restaurant)
        assert list(category.get_available_languages()) == ["en"]
        assert category.safe_translation_getter("name", language_code="en") == "Soups"


@pytest.mark.django_db
class TestModifierGroupForm:
    def test_form_has_name_and_description_fields(self, manager_client):
        html = manager_client.get("/tenant-admin/menu/modifiergroup/add/").content.decode()
        assert 'name="name"' in html
        assert 'name="description"' in html

    def test_add_saves_to_the_current_restaurant(self, manager_client, restaurant):
        # Regression: this exact POST returned 500 (restaurant_id NULL) in production.
        resp = manager_client.post(
            "/tenant-admin/menu/modifiergroup/add/",
            {
                "name": "ზომა",
                "description": "",
                "selection_type": "single",
                "min_selections": "0",
                "max_selections": "1",
                "display_order": "0",
                "is_active": "on",
                **NO_INLINE_MODIFIERS,
            },
        )
        assert resp.status_code == 302, resp.content[:500]

        group = ModifierGroup.objects.get()
        assert group.restaurant == restaurant
        assert group.safe_translation_getter("name", language_code="ka") == "ზომა"


@pytest.mark.django_db
class TestTenantIsolation:
    @pytest.mark.parametrize("url", ["menu/menuitem/add/", "menu/menucategory/add/", "menu/modifiergroup/add/"])
    def test_restaurant_is_never_a_form_field(self, manager_client, rival, url):
        html = manager_client.get(f"/tenant-admin/{url}").content.decode()
        assert 'name="restaurant"' not in html
        assert rival.name not in html

    def test_foreign_key_choices_are_scoped_to_the_restaurant(self, manager_client, restaurant, rival):
        own = _translated(restaurant, ModifierGroup)
        theirs = ModifierGroup(restaurant=rival)
        theirs.set_current_language("ka")
        theirs.name = "RIVAL GROUP"
        theirs.save()

        html = manager_client.get("/tenant-admin/menu/modifier/add/").content.decode()
        assert f'value="{own.pk}"' in html
        assert "RIVAL GROUP" not in html

        # And a hand-crafted POST pointing at the other tenant's group is rejected.
        data = {"name": "დიდი", "price_adjustment": "0", "is_available": "on", "display_order": "0"}
        resp = manager_client.post("/tenant-admin/menu/modifier/add/", {**data, "group": str(theirs.pk)})
        assert resp.status_code == 200  # re-rendered with a validation error
        assert not Modifier.objects.filter(group=theirs).exists()

        resp = manager_client.post("/tenant-admin/menu/modifier/add/", {**data, "group": str(own.pk)})
        assert resp.status_code == 302, resp.content[:500]
        assert Modifier.objects.filter(group=own).exists()


@pytest.mark.django_db
def test_every_tenant_admin_page_renders(admin_user, client, restaurant):
    """
    get_exclude() now hides the restaurant FK on every TenantModelAdmin; a
    model whose fieldsets still named it would raise FieldError. Render each
    registered changelist and add form to prove none does.
    """
    from apps.core.admin_sites import tenant_admin_site

    client.force_login(admin_user)
    client.defaults["HTTP_HOST"] = f"{restaurant.slug}.localhost"

    for model, model_admin in tenant_admin_site._registry.items():
        info = f"{model._meta.app_label}/{model._meta.model_name}"
        changelist = client.get(f"/tenant-admin/{info}/")
        assert changelist.status_code == 200, info
        if model_admin.has_add_permission(changelist.wsgi_request):
            add = client.get(f"/tenant-admin/{info}/add/")
            assert add.status_code == 200, info
