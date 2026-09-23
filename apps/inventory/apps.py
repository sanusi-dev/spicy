from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    label = "inventory"

    def ready(self):
        from django.db.models.signals import post_migrate

        # Seed reference data after migrations.
        def seed_uoms(**kwargs):
            from .models import UOM

            uoms = [
                "Each",
                "Kg",
                "Gram",
                "Litre",
                "Millilitre",
                "Bag",
                "Mudu",
                "Derica",
                "Pack",
                "Carton",
                "Bottle",
                "Can",
                "Dozen",
                "Basin",
                "Paint Tin",
                "Crate",
                "Tray",
                "Sachet",
                "Box",
                "Roll",
            ]
            for name in uoms:
                UOM.objects.get_or_create(name=name)

            from .models import ItemGroup

            groups = [
                "Proteins",
                "Grains & Swallows",
                "Soups & Stews",
                "Sides",
                "Breakfast",
                "Small Chops & Snacks",
                "Soft Drinks",
                "Beer",
                "Spirits",
                "Wine",
                "Water",
                "Juice & Malt",
                "Supplies",
            ]
            for name in groups:
                ItemGroup.objects.get_or_create(name=name)

        post_migrate.connect(seed_uoms)
