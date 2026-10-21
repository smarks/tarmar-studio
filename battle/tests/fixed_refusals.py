"""Fixed words for the battle refusals' tests (issues #97, #86).

The shipped ``config/battle-refusals.toml`` is Spencer's to edit, so no test
pins its sentences. A test that must see what filled a sentence's ``{names}``
runs under :data:`FIXED_REFUSALS`, in which every sentence is its own table and
key followed by the names it may use (``"services.stale_pools {names}"``); any
other test reads the configured words through ``refusal_words()``.
"""

from django.test import override_settings

from battle.refusal_words import BattleRefusalWords
from common.wording import fixed_wording

FIXED_REFUSALS = fixed_wording(BattleRefusalWords)

#: A class or method decorator: run under :data:`FIXED_REFUSALS`.
with_fixed_refusals = override_settings(BATTLE_REFUSAL_WORDS=FIXED_REFUSALS)
