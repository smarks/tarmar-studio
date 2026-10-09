"""Reading and rolling the seeds that make generated content reproducible.

The ruin, hook and encounter generators put their seed in the URL, and a
battle draft stores one, so the same seed always rebuilds the same result.
Each used to parse and roll seeds its own way (issue #337). The pages still
word their own refusals; what they share is here.

Free of Django, so any module can import it.
"""

import secrets

#: Rolled seeds stay small enough to read aloud and retype at the table.
MAX_RANDOM_SEED = 999_999


class InvalidSeed(ValueError):
    """A seed that is not a whole number. The message is fit to show a user."""


def parse_seed(raw: object) -> int | None:
    """Read a seed a user typed or a URL carried.

    Args:
        raw: The submitted value; surrounding whitespace is ignored.

    Returns:
        The seed as an integer, or ``None`` when nothing was given.

    Raises:
        InvalidSeed: If a value was given but is not a whole number.
    """
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise InvalidSeed(f"Seed must be a whole number, not “{text}”.") from None


def roll_seed() -> int:
    """A fresh random seed, from 1 to :data:`MAX_RANDOM_SEED` inclusive."""
    return secrets.randbelow(MAX_RANDOM_SEED) + 1
