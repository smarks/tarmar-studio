"""The one reader for the text files under ``config/``, free of Django.

Wording someone may want to change without Python lives in a text file under
``config/`` (Spencer, 2026-10-17, issue #77): the passphrase gate's hints
(``common.gate.load_gate_hints``) and the sentence a canon rules file's
loader adds when the canon carries a block this studio does not read
(``reference.rules_file``, issue #36). Each file is read the same way, here.
Free of Django because ``reference.rules_file`` is reached by ``gm.engine``'s
Django-free import chain, as ``common.json_text`` is.
"""

from pathlib import Path

# A line starting with this is the file's own commentary, not its content.
COMMENT_PREFIX = "#"


def config_lines(path: Path) -> tuple[str, ...]:
    """The content lines of a configuration text file, in file order.

    Blank lines and lines starting with ``#`` are dropped, so a file can carry
    its own header saying what it is for; every other line is stripped.

    Args:
        path: The file to read, as UTF-8.

    Returns:
        The content lines; empty when the file holds only blanks and comments.

    Raises:
        OSError: The file cannot be read. Each caller says which file it
            needed and why, so the error is theirs to word.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(
        stripped
        for stripped in (line.strip() for line in lines)
        if stripped and not stripped.startswith(COMMENT_PREFIX)
    )
