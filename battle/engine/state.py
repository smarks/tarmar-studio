"""Thin re-export of :mod:`tarmar_engine.state` (#240 milestone 1).

The engine's dataclasses moved to the shared ``tarmar-engine`` package
(https://github.com/smarks/tarmar-engine, pinned in ``pyproject.toml``);
this module keeps the historic import path so nothing downstream changes.
The star import is deliberate: a pure move wants exact namespace parity,
not a curated API.
"""

from tarmar_engine.state import *  # noqa: F403
