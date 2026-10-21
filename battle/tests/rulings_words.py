"""Fixed words for the rulings' tests (issues #867, #37).

The shipped ``config/battle-rulings.toml`` is Spencer's to edit, so no test
pins its sentences: the ruling tests run under :data:`FIXED_WORDS`, in which
every sentence is its own table and key followed by the ``{names}`` it may use
(``"magic_bounds.log_lifted {bounds} {by} {name}"``). Each is distinct, so a
test still proves which sentence was chosen and what filled it. The pattern is
the gate hints' (#77): tests pin the code to its configuration, not to the
file; ``test_wording`` checks the shipped file itself.
"""

from pathlib import Path

from django.conf import settings
from django.test import override_settings

from battle.wording import RulingsWording, load_rulings_wording
from common.wording import fixed_wording

FIXED_WORDS = fixed_wording(RulingsWording)

#: A class or method decorator: run under :data:`FIXED_WORDS`.
with_fixed_words = override_settings(BATTLE_RULINGS_WORDING=FIXED_WORDS)


#: The words the repository ships, whatever ``BATTLE_RULINGS_WORDING_FILE``
#: names: for a golden replay whose hash covers the battle's own lines, so a
#: deployment's wording file cannot move it (#136). Read once, when the
#: decorator is first applied.
SHIPPED_WORDS = load_rulings_wording(
    Path(settings.BASE_DIR) / "config" / "battle-rulings.toml"
)

#: A class or method decorator: run under :data:`SHIPPED_WORDS`.
with_shipped_words = override_settings(BATTLE_RULINGS_WORDING=SHIPPED_WORDS)
