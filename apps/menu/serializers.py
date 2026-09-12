"""
Menu serializers with translation support.
"""

from rest_framework import serializers

from parler_rest.serializers import TranslatableModelSerializer, TranslatedFieldsField

from .models import SELLABLE, MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup


class ModifierSerializer(TranslatableModelSerializer):
    """Serializer for menu modifiers."""

    translations = TranslatedFieldsField(shared_model=Modifier)

    class Meta:
        model = Modifier
        fields = [
            "id",
            "translations",
            "price_adjustment",
            "is_available",
            "is_default",
            "display_order",
        ]
        read_only_fields = ["id"]


class ModifierGroupSerializer(TranslatableModelSerializer):
    """Serializer for modifier groups with nested modifiers."""

    translations = TranslatedFieldsField(shared_model=ModifierGroup)
    modifiers = ModifierSerializer(many=True, read_only=True)

    class Meta:
        model = ModifierGroup
        fields = [
            "id",
            "translations",
            "selection_type",
            "min_selections",
            "max_selections",
            "is_required",
            "display_order",
            "is_active",
            "modifiers",
        ]
        read_only_fields = ["id"]


class ModifierGroupDashboardSerializer(ModifierGroupSerializer):
    """
    Dashboard view of a modifier group: adds the staff-only ``internal_name``.

    Kept separate from ``ModifierGroupSerializer`` because that one is nested in
    the public menu item payload and must never leak the internal label.
    """

    class Meta(ModifierGroupSerializer.Meta):
        fields = ModifierGroupSerializer.Meta.fields + ["internal_name"]


class ModifierGroupListSerializer(TranslatableModelSerializer):
    """Minimal serializer for modifier groups list."""

    translations = TranslatedFieldsField(shared_model=ModifierGroup)

    class Meta:
        model = ModifierGroup
        fields = [
            "id",
            "translations",
            "selection_type",
            "is_required",
            "is_active",
        ]


class MenuCategorySerializer(TranslatableModelSerializer):
    schedule_label = serializers.SerializerMethodField()

    def get_schedule_label(self, obj):
        schedule = getattr(obj, "schedule", None)
        return schedule.label() if schedule is not None else ""

    """Serializer for menu categories."""

    translations = TranslatedFieldsField(shared_model=MenuCategory)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = MenuCategory
        fields = [
            "schedule_label",
            "id",
            "translations",
            "image",
            "image_blurhash",
            "display_order",
            "is_active",
            "items_count",
        ]
        read_only_fields = ["id", "items_count", "image_blurhash"]

    def get_items_count(self, obj):
        return obj.items_count


class MenuCategoryListSerializer(TranslatableModelSerializer):
    """Minimal serializer for category lists."""

    translations = TranslatedFieldsField(shared_model=MenuCategory)

    class Meta:
        model = MenuCategory
        fields = ["id", "translations", "display_order", "is_active"]


def _availability_fields(obj, context) -> dict:
    """available_now / available_from / promo_price / promo_label, computed once per request when possible."""
    from apps.promotions.availability import availability

    cache = context.setdefault("_promo_cache", {}) if isinstance(context, dict) else {}
    restaurant = getattr(obj, "restaurant", None)
    if restaurant is None:
        return {"available_now": True, "available_from": "", "promo_price": None, "promo_label": ""}
    ok, reason = availability(obj, restaurant=restaurant)
    promo_price, promo_label = None, ""
    if getattr(restaurant, "promotions_enabled", False):
        prices = cache.get(("prices", restaurant.pk))
        if prices is None:
            from apps.promotions.services import promo_prices

            channel = context.get("channel", "") if isinstance(context, dict) else ""
            items = cache.get(("items", restaurant.pk)) or [obj]
            prices = promo_prices(restaurant, items, channel=channel)
            cache[("prices", restaurant.pk)] = prices
        hit = prices.get(obj.pk)
        if hit is None and obj.pk not in (cache.get(("seen", restaurant.pk)) or set()):
            from apps.promotions.services import promo_prices

            hit = promo_prices(restaurant, [obj]).get(obj.pk)
            prices[obj.pk] = hit
        if hit:
            promo_price, promo_label = str(hit[0]), hit[1].name
    return {"available_now": ok, "available_from": reason, "promo_price": promo_price, "promo_label": promo_label}


class MenuItemSerializer(TranslatableModelSerializer):
    """Full serializer for menu items."""

    translations = TranslatedFieldsField(shared_model=MenuItem)
    category = MenuCategoryListSerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=MenuCategory.objects.all(),
        source="category",
        write_only=True,
        required=False,
        allow_null=True,
    )
    modifier_groups = serializers.SerializerMethodField()
    dietary_tags = serializers.SerializerMethodField()
    available_now = serializers.SerializerMethodField()
    available_from = serializers.SerializerMethodField()
    promo_price = serializers.SerializerMethodField()
    promo_label = serializers.SerializerMethodField()
    combo_components = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = [
            "id",
            "translations",
            "category",
            "category_id",
            "price",
            "available_now",
            "available_from",
            "promo_price",
            "promo_label",
            "is_combo",
            "combo_components",
            "schedule",
            "unavailable_until",
            "image",
            "image_blurhash",
            "is_available",
            "is_featured",
            "display_order",
            "preparation_time_minutes",
            "preparation_station",
            "calories",
            "allergens",
            "is_vegetarian",
            "is_vegan",
            "is_gluten_free",
            "is_spicy",
            "spicy_level",
            "dietary_tags",
            "track_inventory",
            "stock_quantity",
            "modifier_groups",
        ]
        read_only_fields = ["id", "dietary_tags", "image_blurhash", "unavailable_until"]

    def get_modifier_groups(self, obj):
        links = obj.modifier_groups_link.select_related("modifier_group").all()
        groups = [link.modifier_group for link in links]
        return ModifierGroupSerializer(groups, many=True).data

    def _avail(self, obj):
        key = f"_avail_{obj.pk}"
        if key not in self.context:
            self.context[key] = _availability_fields(obj, self.context)
        return self.context[key]

    def get_available_now(self, obj):
        return self._avail(obj)["available_now"]

    def get_available_from(self, obj):
        return self._avail(obj)["available_from"]

    def get_promo_price(self, obj):
        return self._avail(obj)["promo_price"]

    def get_promo_label(self, obj):
        return self._avail(obj)["promo_label"]

    def get_combo_components(self, obj):
        if not getattr(obj, "is_combo", False):
            return []
        return [
            {
                "menu_item_id": str(c.item_id),
                "name": c.item.safe_translation_getter("name", any_language=True),
                "quantity": c.quantity,
            }
            for c in obj.combo_components.select_related("item")
        ]

    def get_dietary_tags(self, obj):
        return obj.get_dietary_tags()


class MenuItemListSerializer(TranslatableModelSerializer):
    """Minimal serializer for menu item lists."""

    translations = TranslatedFieldsField(shared_model=MenuItem)
    dietary_tags = serializers.SerializerMethodField()
    modifier_groups = serializers.SerializerMethodField()
    available_now = serializers.SerializerMethodField()
    available_from = serializers.SerializerMethodField()
    promo_price = serializers.SerializerMethodField()
    promo_label = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = [
            "id",
            "translations",
            "price",
            "image",
            "image_blurhash",
            "is_available",
            "is_featured",
            "is_combo",
            "available_now",
            "available_from",
            "promo_price",
            "promo_label",
            "dietary_tags",
            "preparation_time_minutes",
            "modifier_groups",
        ]

    def get_dietary_tags(self, obj):
        return obj.get_dietary_tags()

    def _avail(self, obj):
        key = f"_avail_{obj.pk}"
        if key not in self.context:
            self.context[key] = _availability_fields(obj, self.context)
        return self.context[key]

    def get_available_now(self, obj):
        return self._avail(obj)["available_now"]

    def get_available_from(self, obj):
        return self._avail(obj)["available_from"]

    def get_promo_price(self, obj):
        return self._avail(obj)["promo_price"]

    def get_promo_label(self, obj):
        return self._avail(obj)["promo_label"]

    def get_modifier_groups(self, obj):
        links = obj.modifier_groups_link.select_related("modifier_group").all()
        groups = [link.modifier_group for link in links]
        return ModifierGroupSerializer(groups, many=True).data


class MenuItemCreateSerializer(TranslatableModelSerializer):
    """Serializer for creating menu items."""

    translations = TranslatedFieldsField(shared_model=MenuItem)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=MenuCategory.objects.all(),
        source="category",
        required=False,
        allow_null=True,
    )
    modifier_group_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        write_only=True,
    )

    class Meta:
        model = MenuItem
        fields = [
            "translations",
            "category_id",
            "price",
            "image",
            "is_available",
            "is_featured",
            "display_order",
            "preparation_time_minutes",
            "preparation_station",
            "calories",
            "allergens",
            "is_vegetarian",
            "is_vegan",
            "is_gluten_free",
            "is_spicy",
            "spicy_level",
            "track_inventory",
            "stock_quantity",
            "modifier_group_ids",
        ]

    def validate_category_id(self, value):
        """Ensure category belongs to the same restaurant."""
        if value:
            restaurant = self.context.get("restaurant")
            if value.restaurant_id != restaurant.id:
                raise serializers.ValidationError("Category must belong to the same restaurant.")
        return value

    def create(self, validated_data):
        modifier_group_ids = validated_data.pop("modifier_group_ids", [])
        restaurant = self.context.get("restaurant")
        validated_data["restaurant"] = restaurant

        item = super().create(validated_data)

        # Link modifier groups
        for order, group_id in enumerate(modifier_group_ids):
            try:
                group = ModifierGroup.objects.get(id=group_id, restaurant=restaurant)
                MenuItemModifierGroup.objects.create(
                    menu_item=item,
                    modifier_group=group,
                    display_order=order,
                )
            except ModifierGroup.DoesNotExist:
                pass

        return item


class MenuItemUpdateSerializer(TranslatableModelSerializer):
    """Serializer for updating menu items."""

    translations = TranslatedFieldsField(shared_model=MenuItem)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=MenuCategory.objects.all(),
        source="category",
        required=False,
        allow_null=True,
    )
    modifier_group_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        write_only=True,
    )

    class Meta:
        model = MenuItem
        fields = [
            "translations",
            "category_id",
            "price",
            "image",
            "is_available",
            "is_featured",
            "display_order",
            "preparation_time_minutes",
            "preparation_station",
            "calories",
            "allergens",
            "is_vegetarian",
            "is_vegan",
            "is_gluten_free",
            "is_spicy",
            "spicy_level",
            "track_inventory",
            "stock_quantity",
            "modifier_group_ids",
        ]

    def update(self, instance, validated_data):
        modifier_group_ids = validated_data.pop("modifier_group_ids", None)

        instance = super().update(instance, validated_data)

        # Update modifier groups if provided
        if modifier_group_ids is not None:
            instance.modifier_groups_link.all().delete()
            restaurant = instance.restaurant

            for order, group_id in enumerate(modifier_group_ids):
                try:
                    group = ModifierGroup.objects.get(id=group_id, restaurant=restaurant)
                    MenuItemModifierGroup.objects.create(
                        menu_item=instance,
                        modifier_group=group,
                        display_order=order,
                    )
                except ModifierGroup.DoesNotExist:
                    pass

        return instance


class FullMenuSerializer(serializers.Serializer):
    """Serializer for complete restaurant menu with nested structure."""

    categories = serializers.SerializerMethodField()
    uncategorized_items = serializers.SerializerMethodField()

    def __init__(self, restaurant, *args, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def get_categories(self, obj):
        from django.db.models import Prefetch

        categories = (
            MenuCategory.objects.filter(
                restaurant=self.restaurant,
                is_active=True,
            )
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=MenuItem.objects.filter(SELLABLE)
                    .select_related("restaurant", "schedule", "category__schedule")
                    .prefetch_related("modifier_groups_link__modifier_group__modifiers")
                    .order_by("display_order"),
                )
            )
            .select_related("schedule")
            .order_by("display_order")
        )

        result = []
        categories = list(categories)
        all_items = [i for c in categories for i in c.items.all()]
        context = {"_promo_cache": {("items", self.restaurant.pk): all_items}, "channel": "web"}
        for category in categories:
            result.append(
                {
                    "category": MenuCategorySerializer(category).data,
                    "items": MenuItemListSerializer(category.items.all(), many=True, context=context).data,
                }
            )
        return result

    def get_uncategorized_items(self, obj):
        items = (
            MenuItem.objects.filter(
                SELLABLE,
                restaurant=self.restaurant,
                category__isnull=True,
            )
            .prefetch_related("modifier_groups_link__modifier_group__modifiers")
            .order_by("display_order")
        )
        return MenuItemListSerializer(items, many=True).data


class CategoryReorderSerializer(serializers.Serializer):
    """Serializer for reordering categories."""

    category_ids = serializers.ListField(
        child=serializers.UUIDField(),
        help_text="List of category IDs in desired order",
    )

    def save(self, restaurant):
        for order, category_id in enumerate(self.validated_data["category_ids"]):
            MenuCategory.objects.filter(
                id=category_id,
                restaurant=restaurant,
            ).update(display_order=order)


class ModifierGroupCreateSerializer(TranslatableModelSerializer):
    """Serializer for creating modifier groups."""

    translations = TranslatedFieldsField(shared_model=ModifierGroup)
    modifiers = ModifierSerializer(many=True, required=False)

    class Meta:
        model = ModifierGroup
        fields = [
            "translations",
            "internal_name",
            "selection_type",
            "min_selections",
            "max_selections",
            "is_required",
            "display_order",
            "is_active",
            "modifiers",
        ]

    def create(self, validated_data):
        modifiers_data = validated_data.pop("modifiers", [])
        restaurant = self.context.get("restaurant")
        validated_data["restaurant"] = restaurant

        group = super().create(validated_data)

        # Create modifiers
        for order, modifier_data in enumerate(modifiers_data):
            modifier_data["display_order"] = order
            Modifier.objects.create(group=group, **modifier_data)

        return group
