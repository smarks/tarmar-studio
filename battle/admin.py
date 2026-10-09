from django.contrib import admin

from .models import Battle, BattleCombatant, BattleEvent


@admin.register(Battle)
class BattleAdmin(admin.ModelAdmin):
    list_display = ("name", "seed", "status", "turn_number", "created_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "created_by__username")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("created_by",)


@admin.register(BattleCombatant)
class BattleCombatantAdmin(admin.ModelAdmin):
    list_display = ("display_name", "battle", "character", "beast", "team")
    search_fields = ("display_name", "battle__name")
    raw_id_fields = ("battle", "character", "beast")


@admin.register(BattleEvent)
class BattleEventAdmin(admin.ModelAdmin):
    list_display = ("battle", "turn", "phase", "sequence", "event_type", "actor")
    list_filter = ("event_type",)
    search_fields = ("battle__name", "actor", "message")
    raw_id_fields = ("battle",)
