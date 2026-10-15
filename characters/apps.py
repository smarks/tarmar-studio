from django.apps import AppConfig


class CharactersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "characters"

    def ready(self) -> None:
        """Register the system check that loads the canon's character rules
        (issue #59)."""
        from . import checks  # noqa: F401
