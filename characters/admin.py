from django.contrib import admin

from .models import Character


@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "strength",
        "dexterity",
        "intelligence",
        "wisdom",
        "constitution",
        "charisma",
        "is_deleted",
        "created_at",
    )
    list_filter = ("is_deleted", "created_at", "user")
    search_fields = ("name", "user__username")
    readonly_fields = ("created_at",)
    raw_id_fields = ("user",)
    fieldsets = (
        (None, {"fields": ("user", "name")}),
        (
            "Core Attributes",
            {
                "fields": (
                    "strength",
                    "dexterity",
                    "intelligence",
                    "wisdom",
                    "constitution",
                    "charisma",
                )
            },
        ),
        (
            "Physical Attributes",
            {"fields": ("appearance_roll", "height_roll", "weight_roll")},
        ),
        ("Derived", {"fields": ("fatigue_roll",)}),
        ("Metadata", {"fields": ("created_at",)}),
    )
