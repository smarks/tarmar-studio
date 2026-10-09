"""Thin re-export of :mod:`tarmar_engine.policy` (#240 milestone 1).

The utility AI policy moved to the shared ``tarmar-engine`` package; this
module keeps the historic import path so nothing downstream changes. The
star import is deliberate: a pure move wants exact namespace parity, not a
curated API. ``_cast_candidates`` is named explicitly because star imports
skip underscored names and ``test_policy`` exercises it directly.
"""

from tarmar_engine.policy import *  # noqa: F403
from tarmar_engine.policy import _cast_candidates  # noqa: F401
