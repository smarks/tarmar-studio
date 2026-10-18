"""Small counts spelled out, for prose built from data (issue #68).

Django-free, so the rules-built sentences of the sheet and the Games Master's
prompt can use it from any import chain.
"""

_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
)


def number_in_words(number: int) -> str:
    """A count as prose writes it: "four" for 4, "13" for 13.

    Counts up to twelve are spelled out, the usual rule for running prose;
    larger and negative ones stay in figures.

    Args:
        number: The count.

    Returns:
        The word, or the figure.
    """
    if 0 <= number < len(_WORDS):
        return _WORDS[number]
    return str(number)
