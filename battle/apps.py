from django.apps import AppConfig


class BattleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "battle"

    def ready(self):
        # Registers the user_logged_in receiver that adopts a guest's
        # battles into the account they sign into (issue #305).
        from . import adoption  # noqa: F401
