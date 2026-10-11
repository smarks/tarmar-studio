from django.apps import AppConfig


class BattleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "battle"

    def ready(self):
        # Registers the user_logged_in receiver that adopts a guest's
        # battles into the account they sign into (issue #305), and the
        # system check that loads the canon's magic rules (issue #960).
        from . import adoption, checks  # noqa: F401
