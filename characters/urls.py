from django.urls import path

from .views.characters import (
    CharacterEditView,
    CharacterListView,
    DeletedCharactersView,
    OwnerFilterPickerView,
    bulk_delete_characters,
    character_create,
    character_dismiss_import_notice,
    character_import,
    character_mark_dead,
    character_revive,
    character_roll,
    character_roll_fatigue,
    character_roll_literacy,
    character_rule_literacy,
    character_set_pools,
    character_set_visibility,
    character_update_field,
    delete_character,
    restore_character,
)
from .views.combat import character_resolve_attack
from .views.equipment import (
    equipment_add,
    equipment_bulk_remove,
    equipment_catalog,
    equipment_remove,
    equipment_toggle_pack,
    equipment_update,
)
from .views.mechanics import (
    character_grant_mana,
    character_learn_spell,
    character_move_coin,
    character_record_weapon_skill,
    character_roll_survival_save,
    character_skill_check,
)
from .views.skills import (
    character_add_experience_years,
    character_allocate_skill_point,
    character_rename_skill,
)

app_name = "characters"

urlpatterns = [
    path("", CharacterListView.as_view(), name="list"),
    # The list's owner filter, searched a page at a time (#973).
    path("owners/", OwnerFilterPickerView.as_view(), name="owner_picker"),
    path("create/", character_create, name="create"),
    path("import/", character_import, name="import"),
    path("bulk-delete/", bulk_delete_characters, name="bulk_delete"),
    path("<int:pk>/", CharacterEditView.as_view(), name="edit"),
    path("<int:pk>/delete/", delete_character, name="delete"),
    path("<int:pk>/restore/", restore_character, name="restore"),
    path("<int:pk>/mark-dead/", character_mark_dead, name="mark_dead"),
    path("<int:pk>/revive/", character_revive, name="revive"),
    path("<int:pk>/roll-fatigue/", character_roll_fatigue, name="roll_fatigue"),
    path("<int:pk>/roll-literacy/", character_roll_literacy, name="roll_literacy"),
    path("<int:pk>/roll/<str:kind>/", character_roll, name="roll"),
    path("<int:pk>/rule-literacy/", character_rule_literacy, name="rule_literacy"),
    path(
        "<int:pk>/dismiss-import-notice/",
        character_dismiss_import_notice,
        name="dismiss_import_notice",
    ),
    path("<int:pk>/set-pools/", character_set_pools, name="set_pools"),
    path(
        "<int:pk>/set-visibility/",
        character_set_visibility,
        name="set_visibility",
    ),
    path("deleted/", DeletedCharactersView.as_view(), name="deleted"),
    path("<int:pk>/update-field/", character_update_field, name="update_field"),
    # Skills URLs
    path("<int:pk>/add-years/", character_add_experience_years, name="add_years"),
    path(
        "<int:pk>/allocate-skill/",
        character_allocate_skill_point,
        name="allocate_skill",
    ),
    path("<int:pk>/rename-skill/", character_rename_skill, name="rename_skill"),
    path(
        "<int:pk>/weapon-skill/",
        character_record_weapon_skill,
        name="record_weapon_skill",
    ),
    path("<int:pk>/skill-check/", character_skill_check, name="skill_check"),
    # The survival save off the board, for a dying character (issue #839).
    path(
        "<int:pk>/survival-save/",
        character_roll_survival_save,
        name="roll_survival_save",
    ),
    # Coin and magic
    path("<int:pk>/coin/", character_move_coin, name="move_coin"),
    path("<int:pk>/learn-spell/", character_learn_spell, name="learn_spell"),
    path("<int:pk>/grant-mana/", character_grant_mana, name="grant_mana"),
    # Combat
    path("<int:pk>/attack/", character_resolve_attack, name="attack"),
    # Equipment URLs
    path("equipment/catalog/", equipment_catalog, name="equipment_catalog"),
    path("<int:pk>/equipment/add/", equipment_add, name="equipment_add"),
    path("<int:pk>/equipment/update/", equipment_update, name="equipment_update"),
    path("<int:pk>/equipment/remove/", equipment_remove, name="equipment_remove"),
    path(
        "<int:pk>/equipment/bulk-remove/",
        equipment_bulk_remove,
        name="equipment_bulk_remove",
    ),
    path(
        "<int:pk>/equipment/toggle-pack/",
        equipment_toggle_pack,
        name="equipment_toggle_pack",
    ),
]
