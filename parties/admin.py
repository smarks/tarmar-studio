from django.contrib import admin

from .models import Party, PartyMembership


class PartyMembershipInline(admin.TabularInline):
    model = PartyMembership
    extra = 0


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    inlines = [PartyMembershipInline]
