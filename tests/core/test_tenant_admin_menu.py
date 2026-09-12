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

    # Module pages only exist once their module is on; switch everything on.
    from apps.core.modules import MODULES

    for m in MODULES:
        if m.flag:
            setattr(restaurant, m.flag, True)
    restaurant.save()

    client.force_login(admin_user)
    client.defaults["HTTP_HOST"] = f"{restaurant.slug}.localhost"

    for model, model_admin in tenant_admin_site._registry.items():
        info = f"{model._meta.app_label}/{model._meta.model_name}"
        changelist = client.get(f"/tenant-admin/{info}/")
        assert changelist.status_code == 200, info
        if model_admin.has_add_permission(changelist.wsgi_request):
            add = client.get(f"/tenant-admin/{info}/add/")
            assert add.status_code == 200, info


# ---------------------------------------------------------------------------
# Inlines: modifier options under a group, modifier groups on an item, hours.
# Django gates inlines on model-level auth permissions that restaurant staff
# never hold, so every one of these used to vanish for them.
# ---------------------------------------------------------------------------


def _group_with_options(restaurant, names=("პატარა", "დიდი")):
    group = _translated(restaurant, ModifierGroup)
    options = []
    for name in names:
        option = Modifier(group=group, price_adjustment=0)
        option.set_current_language("ka")
        option.name = name
        option.save()
        options.append(option)
    return group, options


GROUP_FIELDS = {
    "description": "",
    "selection_type": "single",
    "min_selections": "0",
    "max_selections": "1",
    "display_order": "0",
    "is_active": "on",
}


def _option_rows(options, names, new=None):
    """Management form + one row per existing option, plus an optional new row."""
    total = len(options) + (1 if new is not None else 0)
    data = {
        "modifiers-TOTAL_FORMS": str(total),
        "modifiers-INITIAL_FORMS": str(len(options)),
        "modifiers-MIN_NUM_FORMS": "0",
        "modifiers-MAX_NUM_FORMS": "1000",
    }
    rows = [(str(o.pk), n, "0") for o, n in zip(options, names)]
    if new is not None:  # "" is a real (blank-named) new row
        rows.append(("", new, "1.50"))
    for i, (pk, name, price) in enumerate(rows):
        data.update(
            {
                f"modifiers-{i}-id": pk,
                f"modifiers-{i}-name": name,
                f"modifiers-{i}-price_adjustment": price,
                f"modifiers-{i}-is_available": "on",
                f"modifiers-{i}-display_order": str(i),
            }
        )
    return data


def _name_in(option, code):
    return option.safe_translation_getter("name", language_code=code, any_language=False)


@pytest.mark.django_db
class TestModifierOptionsInline:
    def test_manager_sees_option_rows_on_every_language_tab(self, manager_client, restaurant):
        group, options = _group_with_options(restaurant)
        for code in LANGS:
            html = manager_client.get(
                f"/tenant-admin/menu/modifiergroup/{group.pk}/change/?language={code}"
            ).content.decode()
            assert "modifiers-TOTAL_FORMS" in html, code
            # 2 existing rows + 3 blank extra rows
            assert len(re.findall(r'name="modifiers-\d+-name"', html)) == 5, code

    def test_english_names_are_saved_and_georgian_kept(self, manager_client, restaurant):
        group, options = _group_with_options(restaurant)
        resp = manager_client.post(
            f"/tenant-admin/menu/modifiergroup/{group.pk}/change/?language=en",
            {**GROUP_FIELDS, "name": "Size", **_option_rows(options, ["Small", "Large"])},
        )
        assert resp.status_code == 302, resp.content[:800]

        small, large = (Modifier.objects.get(pk=o.pk) for o in options)
        assert (_name_in(small, "en"), _name_in(small, "ka")) == ("Small", "პატარა")
        assert (_name_in(large, "en"), _name_in(large, "ka")) == ("Large", "დიდი")

    def test_rows_left_blank_do_not_block_the_save(self, manager_client, restaurant):
        group, options = _group_with_options(restaurant)
        resp = manager_client.post(
            f"/tenant-admin/menu/modifiergroup/{group.pk}/change/?language=ru",
            {**GROUP_FIELDS, "name": "Размер", **_option_rows(options, ["", ""])},
        )
        assert resp.status_code == 302, resp.content[:800]

        group = ModifierGroup.objects.get(pk=group.pk)
        assert _name_in(group, "ru") == "Размер"
        for option in options:
            option = Modifier.objects.get(pk=option.pk)
            assert list(option.get_available_languages()) == ["ka"]  # no empty Russian row created

    def test_new_option_added_from_the_english_tab(self, manager_client, restaurant):
        group, options = _group_with_options(restaurant)
        resp = manager_client.post(
            f"/tenant-admin/menu/modifiergroup/{group.pk}/change/?language=en",
            {**GROUP_FIELDS, "name": "Size", **_option_rows(options, ["Small", "Large"], new="Extra large")},
        )
        assert resp.status_code == 302, resp.content[:800]

        new = Modifier.objects.filter(group=group).exclude(pk__in=[o.pk for o in options]).get()
        assert list(new.get_available_languages()) == ["en"]
        assert str(new.price_adjustment) == "1.50"

        # Back on the Georgian tab it shows up with an empty name; leaving it
        # blank is fine, and Georgian visitors fall back to the English text.
        html = manager_client.get(f"/tenant-admin/menu/modifiergroup/{group.pk}/change/?language=ka").content.decode()
        assert f'value="{new.pk}"' in html
        resp = manager_client.post(
            f"/tenant-admin/menu/modifiergroup/{group.pk}/change/?language=ka",
            {**GROUP_FIELDS, "name": "ზომა", **_option_rows(options + [new], ["პატარა", "დიდი", ""])},
        )
        assert resp.status_code == 302, resp.content[:800]
        new = Modifier.objects.get(pk=new.pk)
        assert list(new.get_available_languages()) == ["en"]
        new.set_current_language("ka")
        assert new.name == "Extra large"

    def test_a_new_row_still_needs_a_name(self, manager_client, restaurant):
        group, options = _group_with_options(restaurant)
        resp = manager_client.post(
            f"/tenant-admin/menu/modifiergroup/{group.pk}/change/?language=en",
            {**GROUP_FIELDS, "name": "Size", **_option_rows(options, ["Small", "Large"], new="")},
        )
        assert resp.status_code == 200  # re-rendered with the error
        assert Modifier.objects.filter(group=group).count() == 2


@pytest.mark.django_db
class TestModifierGroupsOnItemInline:
    def test_manager_can_attach_own_group_but_not_a_rivals(self, manager_client, restaurant, rival):
        from apps.menu.models import MenuItemModifierGroup

        item = _translated(restaurant, MenuItem, price="9.50")
        own, _ = _group_with_options(restaurant)
        theirs, _ = _group_with_options(rival)

        html = manager_client.get(f"/tenant-admin/menu/menuitem/{item.pk}/change/").content.decode()
        assert "modifier_groups_link-TOTAL_FORMS" in html

        item_fields = {
            "name": "ხინკალი",
            "description": "",
            "price": "9.50",
            "is_available": "on",
            "display_order": "0",
            "preparation_time_minutes": "15",
            "preparation_station": "kitchen",
            "spicy_level": "0",
            "stock_quantity": "0",
        }
        mgmt = {
            "modifier_groups_link-TOTAL_FORMS": "1",
            "modifier_groups_link-INITIAL_FORMS": "0",
            "modifier_groups_link-MIN_NUM_FORMS": "0",
            "modifier_groups_link-MAX_NUM_FORMS": "1000",
            "modifier_groups_link-0-id": "",
            "modifier_groups_link-0-display_order": "0",
        }

        resp = manager_client.post(
            f"/tenant-admin/menu/menuitem/{item.pk}/change/",
            {**item_fields, **mgmt, "modifier_groups_link-0-modifier_group": str(theirs.pk)},
        )
        assert resp.status_code == 200  # rejected: not one of this restaurant's groups
        assert not MenuItemModifierGroup.objects.filter(menu_item=item).exists()

        resp = manager_client.post(
            f"/tenant-admin/menu/menuitem/{item.pk}/change/",
            {**item_fields, **mgmt, "modifier_groups_link-0-modifier_group": str(own.pk)},
        )
        assert resp.status_code == 302, resp.content[:800]
        assert MenuItemModifierGroup.objects.filter(menu_item=item, modifier_group=own).exists()


@pytest.mark.django_db
def test_owner_sees_opening_hours_inline(client, restaurant, staff_roles, create_staff_member):
    owner_role = next(r for r in staff_roles if r.name == "owner")
    create_staff_member(user=restaurant.owner, restaurant=restaurant, role=owner_role)
    client.force_login(restaurant.owner)
    client.defaults["HTTP_HOST"] = f"{restaurant.slug}.localhost"

    html = client.get(f"/tenant-admin/tenants/restaurant/{restaurant.pk}/change/").content.decode()
    assert re.search(r'name="\w+-TOTAL_FORMS"', html), "hours inline missing"
    assert len(re.findall(r'name="\w+-\d+-day_of_week"', html)) == 7


@pytest.mark.django_db
class TestLanguageFallbacks:
    def test_row_created_in_english_only_renders_everywhere(self, manager_client, restaurant):
        item = MenuItem(restaurant=restaurant, price="5")
        item.set_current_language("en")
        item.name = "Khinkali"
        item.save()

        # Georgian admin UI: changelist must not raise TranslationDoesNotExist.
        html = manager_client.get("/tenant-admin/menu/menuitem/").content.decode()
        assert "Khinkali" in html

        item = MenuItem.objects.get(pk=item.pk)
        item.set_current_language("ru")
        assert item.name == "Khinkali"

    def test_forms_open_on_the_restaurants_default_language(self, manager_client, restaurant):
        restaurant.default_language = "en"
        restaurant.save(update_fields=["default_language"])

        html = manager_client.get("/tenant-admin/menu/menuitem/add/").content.decode()
        assert re.search(r'aria-current="page"[^>]*data-language="en"', html)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "/admin/menu/menuitem/add/",
        "/admin/tenants/city/add/",
        "/admin/tenants/restaurantcategory/add/",
        "/admin/tenants/amenity/add/",
    ],
)
def test_platform_admin_forms_have_their_translated_name_field(admin_client, url):
    html = admin_client.get(url).content.decode()
    assert 'name="name"' in html, url


# ---------------------------------------------------------------------------
# Modifier groups: staff-only internal name, and the picker that uses it.
# ---------------------------------------------------------------------------

PICKER_URL = "/tenant-admin/autocomplete/?app_label=menu&model_name=menuitemmodifiergroup&field_name=modifier_group"


@pytest.mark.django_db
class TestModifierGroupInternalName:
    def test_internal_name_is_saved_and_listed(self, manager_client, restaurant):
        resp = manager_client.post(
            "/tenant-admin/menu/modifiergroup/add/",
            {
                "internal_name": "ქათმის ხვეულა - ექსტრა",
                "name": "ექსტრა",
                "description": "",
                "selection_type": "multiple",
                "min_selections": "0",
                "max_selections": "3",
                "display_order": "0",
                "is_active": "on",
                **NO_INLINE_MODIFIERS,
            },
        )
        assert resp.status_code == 302, resp.content[:800]

        group = ModifierGroup.objects.get(restaurant=restaurant)
        assert group.internal_name == "ქათმის ხვეულა - ექსტრა"
        assert group.admin_label == "ქათმის ხვეულა - ექსტრა"
        assert "ქათმის ხვეულა - ექსტრა" in manager_client.get("/tenant-admin/menu/modifiergroup/").content.decode()

    def test_picker_shows_internal_name_customer_name_and_options(self, manager_client, restaurant, rival):
        group, _ = _group_with_options(restaurant)  # ka name "ხინკალი", options პატარა / დიდი
        group.internal_name = "ქათმის ხვეულა - ექსტრა"
        group.save(update_fields=["internal_name"])
        theirs, _ = _group_with_options(rival)
        theirs.internal_name = "RIVAL ხვეულა"
        theirs.save(update_fields=["internal_name"])

        resp = manager_client.get(PICKER_URL + "&term=ხვეულა")
        assert resp.status_code == 200, resp.content[:300]
        texts = [r["text"] for r in resp.json()["results"]]

        assert texts == ["ქათმის ხვეულა - ექსტრა · shows as: ხინკალი · options: პატარა, დიდი"]
        assert not any("RIVAL" in t for t in texts)

    def test_picker_falls_back_to_customer_name(self, manager_client, restaurant):
        _group_with_options(restaurant, names=())
        resp = manager_client.get(PICKER_URL + "&term=ხინკალი")
        assert [r["text"] for r in resp.json()["results"]] == ["ხინკალი"]
