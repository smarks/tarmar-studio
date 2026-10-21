"""Find refusals written in code rather than read from configuration (#97).

The refusal-words tests of each app hand :func:`literal_refusals` a module and
the calls whose arguments a player reads; it reports every argument whose words
are fixed in the source: a string literal or f-string, a module-level string
constant passed by name (the idiom #97 removed), ``.format()`` called on either,
and a ``+`` or ``%`` built from one. Arguments are read positionally and by
keyword alike.
"""

import ast
from pathlib import Path


def _module_strings(tree: ast.Module) -> set[str]:
    """The names a module binds, at its top level, to a string literal."""
    names: set[str] = set()
    for statement in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets, value = [statement.target], statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            names.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
    return names


def fixed_words(node: ast.expr, constants: set[str]) -> bool:
    """Whether ``node``'s words are fixed in the source.

    Args:
        node: An expression handed to a refusing call, or returned as one.
        constants: The module's top-level string constants.
    """
    if isinstance(node, ast.Constant):
        # Punctuation alone (the full stop a note is closed with) is not
        # words a reader could want changed; a letter makes it words (#136).
        return isinstance(node.value, str) and any(
            character.isalpha() for character in node.value
        )
    if isinstance(node, ast.IfExp):
        # ``said.a if many else "…"``: either branch can be what is read.
        return fixed_words(node.body, constants) or fixed_words(node.orelse, constants)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Name):
        return node.id in constants
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return fixed_words(node.left, constants) or fixed_words(node.right, constants)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        return fixed_words(node.func.value, constants)
    return False


def literal_refusals(
    module: Path, calls: set[str], structure_keywords: frozenset[str] = frozenset()
) -> list[str]:
    """Each argument of a refusing call in ``module`` whose words are fixed.

    Args:
        module: The source file.
        calls: The names (a function's, or a method's attribute) of the calls
            whose arguments are words for the player.
        structure_keywords: Keyword arguments of those calls that carry
            program structure, not words (``field="name"``), and are skipped.

    Returns:
        ``"<file>:<line>"`` for each one; empty when every refusal reads its
        words from configuration.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    constants = _module_strings(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", getattr(node.func, "attr", None))
        if name not in calls:
            continue
        arguments = [
            *node.args,
            *(
                keyword.value
                for keyword in node.keywords
                if keyword.arg not in structure_keywords
            ),
        ]
        found.extend(
            f"{module.name}:{argument.lineno}"
            for argument in arguments
            if fixed_words(argument, constants)
        )
    return found


def literal_returns(module: Path, functions: set[str]) -> list[str]:
    """Each ``return`` in ``functions`` of ``module`` whose words are fixed.

    For the functions that build a line the page or the log shows and return
    it, rather than hand it to a refusing call (issue #136).

    Args:
        module: The source file.
        functions: The names of the functions to read.

    Returns:
        ``"<file>:<line>"`` for each one; empty when every line they return
        reads its words from configuration.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    constants = _module_strings(tree)
    return [
        f"{module.name}:{node.lineno}"
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef) and function.name in functions
        for node in ast.walk(function)
        if isinstance(node, ast.Return)
        and node.value is not None
        and fixed_words(node.value, constants)
    ]
