from django.urls import path

from .luck import gm_spend_luck
from .views import (
    GMChatView,
    GMMapView,
    GMReviewListView,
    gm_campaign_panel,
    gm_characters_attach,
    gm_characters_detach,
    gm_characters_panel,
    gm_continue,
    gm_delete,
    gm_fight,
    gm_mode,
    gm_review_detail,
    gm_review_statuses,
    gm_save,
    gm_stream,
    gm_transcript,
    gm_turn,
)
from .views_records import (
    gm_entities,
    gm_fight_verdict,
    gm_recap,
    gm_rulings,
    gm_threads,
)
from .withdraw import gm_character_withdraw

app_name = "gm"

urlpatterns = [
    path("", GMChatView.as_view(), name="chat"),
    path("map/", GMMapView.as_view(), name="map"),
    path("stream/", gm_stream, name="stream"),
    path("turns/<int:turn_id>/", gm_turn, name="turn"),
    path("save/", gm_save, name="save"),
    path("delete/", gm_delete, name="delete"),
    path("continue/", gm_continue, name="continue"),
    path("mode/", gm_mode, name="mode"),
    path("sessions/<int:session_id>/transcript/", gm_transcript, name="transcript"),
    # GET lists the reviews (issue #572); POST sends a session (issue #402).
    path("reviews/", GMReviewListView.as_view(), name="reviews"),
    path("reviews/statuses/", gm_review_statuses, name="review_statuses"),
    path("reviews/<int:review_id>/", gm_review_detail, name="review"),
    path("characters/attach/", gm_characters_attach, name="characters_attach"),
    path("characters/detach/", gm_characters_detach, name="characters_detach"),
    # A player takes their character out of a Game Master's session (#871).
    path(
        "characters/<int:character_id>/withdraw/",
        gm_character_withdraw,
        name="character_withdraw",
    ),
    path(
        "sessions/<int:session_id>/characters/",
        gm_characters_panel,
        name="characters_panel",
    ),
    path(
        "sessions/<int:session_id>/campaign/",
        gm_campaign_panel,
        name="campaign_panel",
    ),
    path("fight/", gm_fight, name="fight"),
    # How a session fight ended, filed as a notice (issue #785).
    path("fight/verdict/", gm_fight_verdict, name="fight_verdict"),
    # The Game Master's hand edits of the campaign's records (#789-#791).
    path("sessions/<int:session_id>/rulings/", gm_rulings, name="rulings"),
    path("sessions/<int:session_id>/entities/", gm_entities, name="entities"),
    path("sessions/<int:session_id>/threads/", gm_threads, name="threads"),
    path("sessions/<int:session_id>/recap/", gm_recap, name="recap"),
    # Luck, once per session per level (issue #861).
    path("sessions/<int:session_id>/luck/", gm_spend_luck, name="spend_luck"),
]
