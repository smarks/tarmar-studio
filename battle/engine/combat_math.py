"""Thin re-export of :mod:`tarmar_engine.combat_math` (#240 milestone 1).

The attack arithmetic moved to the shared ``tarmar-engine`` package; this
module keeps the historic import path so nothing downstream changes. The
star import is deliberate: a pure move wants exact namespace parity, not a
curated API.
"""

from tarmar_engine.combat_math import *  # noqa: F403
