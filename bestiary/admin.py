from django.contrib import admin

from .models import Beast


@admin.register(Beast)
class BeastAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "strength",
        "dexterity",
        "armour_tier",
        "size_hexes",
        "source_page",
        "is_generated",
    )
    list_filter = ("category", "is_generated", "armour_tier")
    search_fields = ("name", "description", "special_abilities")
    prepopulated_fields = {"slug": ("name",)}
