from django import template

from accounts.models import User

register = template.Library()


@register.simple_tag
def role_names(user) -> list[str]:
    """Return the display names of every role this user holds, in site order."""
    roles = []
    if user.is_role_admin:
        roles.append("Admin")
    if user.is_dm:
        roles.append("DM")
    if user.is_player:
        roles.append("Player")
    return roles


@register.simple_tag
def display_roles(user) -> str:
    """Display user roles as a comma-separated string."""
    return ", ".join(role_names(user)) or "None"


@register.simple_tag
def contact_choices():
    """Return the contact choices from the User model."""
    return User.CONTACT_CHOICES


@register.simple_tag
def contact_choices_with_empty():
    """Return contact choices with an empty option for forms."""
    return [("", "---")] + list(User.CONTACT_CHOICES)
