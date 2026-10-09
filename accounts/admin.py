from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "real_name",
        "email",
        "is_role_admin",
        "is_dm",
        "is_player",
    )
    list_filter = ("is_role_admin", "is_dm", "is_player")
    fieldsets = BaseUserAdmin.fieldsets + (  # type: ignore[reportAssignmentType]  # django-stubs types UserAdmin.fieldsets as a fixed-arity tuple, so concatenating extra fieldset tuples doesn't type-check even though it works at runtime
        ("Profile", {"fields": ("real_name", "phone", "discord", "preferred_contact")}),
        ("Roles", {"fields": ("is_role_admin", "is_dm", "is_player")}),
    )
