"""Items joined into running prose: "A", "A and B", "A, B and C".

The one joiner for every list the sheet, the battle page, the Games Master's
prompt and the gait wording put into a sentence (tarmar-studio #208). The
word before the last item is the caller's: the battle page's is
configuration (``common.and_word`` in ``config/battle-rulings.toml``), and the
prompt's is its own (``characters.rules.PROMPT_AND_WORD``). Django-free, so
the prompt's offline import chain can use it.
"""

from collections.abc import Sequence


def listed_in_words(
    items: Sequence[str], *, and_word: str, serial_comma: bool = False
) -> str:
    """``items`` joined by commas, with ``and_word`` before the last.

    Args:
        items: The items, in the order to name them.
        and_word: The word before the last item: "and", "or".
        serial_comma: Put a comma before ``and_word`` too when there are
            three items or more ("A, B, or C").

    Returns:
        The joined items; the one item alone; ``""`` for none.
    """
    if len(items) < 2:
        return "".join(items)
    comma = "," if serial_comma and len(items) > 2 else ""
    return f"{', '.join(items[:-1])}{comma} {and_word} {items[-1]}"
