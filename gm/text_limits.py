"""Each text a Game Master's page takes by hand, and its limit (issue #938).

The GM side of #928. The Campaign tab, a hand-set person or thing, the Luck
control and the recap correction typed their ``maxlength`` out as literal
numbers, which matched the server only until a limit changed. The pages read
``gm_text_limits.<field>`` from :data:`GM_TEXT_LIMITS` instead, whose numbers
are the model columns' own ``max_length`` -- or, for a text with no column of
its own, the constant its service refuses by -- so an input stops where the
server would refuse, and nowhere else.
"""

from common.helpers import column_length

from .luck import MAX_ROLL_NOTE
from .models import SceneEntity, StoryThread, TableRuling
from .summary import MAX_SUMMARY_CHARS

#: Each hand-typed GM text's limit by the name the pages read it under. Every
#: path that stores one refuses a longer value with a line the page shows
#: (``gm.hand_records``, ``gm.luck.spend_luck``); none of them cuts it short.
GM_TEXT_LIMITS: dict[str, int] = {
    "ruling_topic": column_length(TableRuling, "topic"),
    "ruling_text": column_length(TableRuling, "text"),
    "thread_text": column_length(StoryThread, "text"),
    "thread_resolution": column_length(StoryThread, "resolution"),
    "entity_name": column_length(SceneEntity, "name"),
    "entity_where": column_length(SceneEntity, "where"),
    "entity_wants": column_length(SceneEntity, "wants"),
    "entity_condition": column_length(SceneEntity, "condition"),
    "entity_notes": column_length(SceneEntity, "notes"),
    # Held in the history line a spend writes, not a column of its own.
    "luck_roll": MAX_ROLL_NOTE,
    # GMSession.summary is a TextField; the recap's limit is the fold's.
    "fold_recap": MAX_SUMMARY_CHARS,
}
