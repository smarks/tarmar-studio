"""Thin re-export of :mod:`tarmar_engine.spells` (#240 milestone 1).

The spell definitions moved to the shared ``tarmar-engine`` package; this
module keeps the historic import path so nothing downstream changes. The
star import is deliberate: a pure move wants exact namespace parity, not a
curated API.
"""

from tarmar_engine.spells import *  # noqa: F403
