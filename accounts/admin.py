from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.forms import ModelForm
from django.http import HttpRequest

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
        # Issue #931: password reset mails only a confirmed address. An
        # address typed here is not proved by being typed (save_model).
        (
            "Email confirmation",
            {"fields": ("email_verified", "pending_email", "pending_email_sent_at")},
        ),
    )
    readonly_fields = ("pending_email", "pending_email_sent_at")

    def save_model(
        self, request: HttpRequest, obj: User, form: ModelForm, change: bool
    ) -> None:
        """Save, unconfirming an address changed without touching the flag.

        Password reset mails only a confirmed address (issue #931), and an
        address is not confirmed by being typed into this form. When the same
        save also changes ``email_verified``, the operator has said what it
        should be, and that stands.

        Args:
            request: The admin request.
            obj: The account being saved.
            form: The bound change form.
            change: True when editing an existing account.
        """
        if "email" in form.changed_data and "email_verified" not in form.changed_data:
            obj.email_verified = False
        super().save_model(request, obj, form, change)
