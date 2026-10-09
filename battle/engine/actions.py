"""Thin re-export of :mod:`tarmar_engine.actions` (#240 milestone 1).

The action-option catalog moved to the shared ``tarmar-engine`` package;
this module keeps the historic import path so nothing downstream changes.
The star import is deliberate: a pure move wants exact namespace parity,
not a curated API.
"""

from tarmar_engine.actions import *  # noqa: F403
