"""
Menu serializers with translation support.
"""

from parler_rest.serializers import TranslatableModelSerializer, TranslatedFieldsField
from rest_framework import serializers

from .models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup


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
    """Serializer for menu categories."""

    translations = TranslatedFieldsField(shared_model=MenuCategory)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = MenuCategory
        fields = [
            "id",
            "translations",
            "image",
            "display_order",
            "is_active",
            "items_count",
        ]
        read_only_fields = ["id", "items_count"]

    def get_items_count(self, obj):
        return obj.items_count


class MenuCategoryListSerializer(TranslatableModelSerializer):
    """Minimal serializer for category lists."""

    translations = TranslatedFieldsField(shared_model=MenuCategory)

    class Meta:
        model = MenuCategory
        fields = ["id", "translations", "display_order", "is_active"]


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

    class Meta:
        model = MenuItem
        fields = [
            "id",
            "translations",
            "category",
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
            "dietary_tags",
            "track_inventory",
            "stock_quantity",
            "modifier_groups",
        ]
        read_only_fields = ["id", "dietary_tags"]

    def get_modifier_groups(self, obj):
        links = obj.modifier_groups_link.select_related("modifier_group").all()
        groups = [link.modifier_group for link in links]
        return ModifierGroupSerializer(groups, many=True).data

    def get_dietary_tags(self, obj):
        return obj.get_dietary_tags()


class MenuItemListSerializer(TranslatableModelSerializer):
    """Minimal serializer for menu item lists."""

    translations = TranslatedFieldsField(shared_model=MenuItem)
    dietary_tags = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = [
            "id",
            "translations",
            "price",
            "image",
            "is_available",
            "is_featured",
            "dietary_tags",
            "preparation_time_minutes",
        ]

    def get_dietary_tags(self, obj):
        return obj.get_dietary_tags()


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
        categories = MenuCategory.objects.filter(
            restaurant=self.restaurant,
            is_active=True,
        ).prefetch_related("items").order_by("display_order")

        result = []
        for category in categories:
            items = category.items.filter(is_available=True).order_by("display_order")
            result.append({
                "category": MenuCategorySerializer(category).data,
                "items": MenuItemListSerializer(items, many=True).data,
            })
        return result

    def get_uncategorized_items(self, obj):
        items = MenuItem.objects.filter(
            restaurant=self.restaurant,
            category__isnull=True,
            is_available=True,
        ).order_by("display_order")
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
