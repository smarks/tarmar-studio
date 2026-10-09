from django.urls import path

from .views import (
    BattleDetailView,
    BattleListView,
    battle_act,
    battle_advance,
    battle_control,
    battle_delete,
    battle_rename,
    battle_resnapshot,
    battle_restart,
    battle_restore,
    battle_setup,
    battle_setup_add,
    battle_setup_create,
    battle_setup_discard,
    battle_setup_entry_team,
    battle_setup_import_party,
    battle_setup_remove,
    battle_setup_start,
    battle_setup_team_add,
    battle_setup_team_remove,
    battle_setup_update,
    battle_start,
    battle_state,
    combatant_control,
    seat_claim,
    seat_ready,
    seat_release,
    seat_return,
    seat_take,
)

app_name = "battle"

urlpatterns = [
    path("", BattleListView.as_view(), name="list"),
    # The staged setup flow (#270/#271): a draft with its own page replaces
    # the old inline creation form.
    path("setup/", battle_setup_create, name="setup-create"),
    path("setup/<int:pk>/", battle_setup, name="setup"),
    path("setup/<int:pk>/update/", battle_setup_update, name="setup-update"),
    path("setup/<int:pk>/add/", battle_setup_add, name="setup-add"),
    path("setup/<int:pk>/remove/", battle_setup_remove, name="setup-remove"),
    # Teams on the muster (#275): import a party as one, name or disband
    # one, move an entry between them.
    path(
        "setup/<int:pk>/import-party/",
        battle_setup_import_party,
        name="setup-import-party",
    ),
    path("setup/<int:pk>/teams/add/", battle_setup_team_add, name="setup-team-add"),
    path(
        "setup/<int:pk>/teams/remove/",
        battle_setup_team_remove,
        name="setup-team-remove",
    ),
    path(
        "setup/<int:pk>/entry-team/",
        battle_setup_entry_team,
        name="setup-entry-team",
    ),
    path("setup/<int:pk>/start/", battle_setup_start, name="setup-start"),
    path("setup/<int:pk>/discard/", battle_setup_discard, name="setup-discard"),
    path("<int:pk>/", BattleDetailView.as_view(), name="detail"),
    path("<int:pk>/advance/", battle_advance, name="advance"),
    path("<int:pk>/rename/", battle_rename, name="rename"),
    path("<int:pk>/restart/", battle_restart, name="restart"),
    path("<int:pk>/resnapshot/", battle_resnapshot, name="resnapshot"),
    # Soft delete and its way back (#339, item 5), the shape characters
    # already use: the row and its event log survive a delete.
    path("<int:pk>/delete/", battle_delete, name="delete"),
    path("<int:pk>/restore/", battle_restore, name="restore"),
    path("<int:pk>/start/", battle_start, name="start"),
    path("<int:pk>/act/", battle_act, name="act"),
    path("<int:pk>/state/", battle_state, name="state"),
    # Who decides (#308): the battle's default, and one combatant's override.
    path("<int:pk>/control/", battle_control, name="control"),
    path(
        "<int:pk>/combatants/<int:combatant_id>/control/",
        combatant_control,
        name="combatant-control",
    ),
    path("<int:pk>/seats/<int:combatant_id>/claim/", seat_claim, name="seat-claim"),
    path(
        "<int:pk>/seats/<int:combatant_id>/release/",
        seat_release,
        name="seat-release",
    ),
    path("<int:pk>/seats/<int:combatant_id>/ready/", seat_ready, name="seat-ready"),
    # A Game Master stands in for an absent owner, and the seat goes back
    # (#853).
    path("<int:pk>/seats/<int:combatant_id>/take/", seat_take, name="seat-take"),
    path(
        "<int:pk>/seats/<int:combatant_id>/return/",
        seat_return,
        name="seat-return",
    ),
]
