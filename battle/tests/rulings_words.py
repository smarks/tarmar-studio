"""Fixed words for the rulings' tests (issues #867, #37).

The shipped ``config/battle-rulings.toml`` is Spencer's to edit, so no test
pins its sentences: the ruling tests run under :data:`FIXED_WORDS`, in which
every sentence is its own table and key followed by the ``{names}`` it may use
(``"magic_bounds.log_lifted {bounds} {by} {name}"``). Each is distinct, so a
test still proves which sentence was chosen and what filled it. The pattern is
the gate hints' (#77): tests pin the code to its configuration, not to the
file; ``test_wording`` checks the shipped file itself.
"""

from dataclasses import fields
from typing import get_type_hints

from django.test import override_settings

from battle.wording import PLACEHOLDERS, RulingsWording


def fixed_rulings_wording() -> RulingsWording:
    """Every sentence as ``"<table>.<key> {name} ..."``."""
    tables = {}
    for table, words_type in get_type_hints(RulingsWording).items():
        tables[table] = words_type(
            **{
                word_field.name: " ".join(
                    [
                        f"{table}.{word_field.name}",
                        *(
                            f"{{{placeholder}}}"
                            for placeholder in sorted(word_field.metadata[PLACEHOLDERS])
                        ),
                    ]
                )
                for word_field in fields(words_type)
            }
        )
    return RulingsWording(**tables)


FIXED_WORDS = fixed_rulings_wording()

#: A class or method decorator: run under :data:`FIXED_WORDS`.
with_fixed_words = override_settings(BATTLE_RULINGS_WORDING=FIXED_WORDS)
