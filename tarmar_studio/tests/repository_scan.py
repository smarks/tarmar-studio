"""Which directories a whole-repository scan in a test skips.

The template-comment test and the attribute-list test both walk the checkout
and have to leave dependencies and build output alone. They share this one
list so the two cannot drift apart.
"""

#: Directory names holding installed packages, build output or caches rather
#: than this repository's own source.
SKIPPED_DIRECTORY_NAMES = frozenset(
    {
        ".venv",
        "__pycache__",
        "build",
        "node_modules",
        "staticfiles",
        "test-results",
    }
)
