from django.apps import AppConfig


class GmConfig(AppConfig):
    name = "gm"

    def ready(self) -> None:
        """Hear when a battle ends, to tell a session's player (issue #785)."""
        from battle.signals import battle_ended

        from .battle_report import file_verdicts_for_ended_battle

        battle_ended.connect(
            file_verdicts_for_ended_battle, dispatch_uid="gm-fight-verdict"
        )
