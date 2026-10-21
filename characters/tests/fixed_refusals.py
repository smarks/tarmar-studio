"""Fixed words for the characters refusals' tests (issue #85).

The shipped ``config/characters-refusals.toml`` is Spencer's to edit, so no
test pins its sentences. A test that must see what filled a sentence's
``{names}`` (the canon's dice in a field's reason) runs under
:data:`FIXED_REFUSALS`, in which every sentence is its own table and key
followed by the names it may use (``"services.field_attribute {dice}
{points}"``); any other test reads the configured words through
``refusal_words()``.
"""

from django.test import override_settings

from characters.refusal_words import CharactersRefusalWords
from common.wording import fixed_wording

FIXED_REFUSALS = fixed_wording(CharactersRefusalWords)

#: A class or method decorator: run under :data:`FIXED_REFUSALS`.
with_fixed_refusals = override_settings(CHARACTERS_REFUSAL_WORDS=FIXED_REFUSALS)
