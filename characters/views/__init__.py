"""Character view endpoints, split by concern (issue #90):

- ``.characters`` -- CRUD, creation, import, soft-delete, single-field edits.
- ``.skills`` -- experience-term and skill-allocation endpoints.
- ``.equipment`` -- equipment endpoints (item building/validation lives in
  ``characters/equipment_service.py``, not here).
- ``.combat`` -- attack resolution.

``characters/urls.py`` imports directly from these submodules. This
top-level package only re-exports the one name another app reaches into
(``battle/generation.py`` shares the character-creation stat list) so that
import keeps working without reaching past the package boundary into a
specific view module.
"""

from .characters import _CREATION_STAT_FIELDS

__all__ = ["_CREATION_STAT_FIELDS"]
