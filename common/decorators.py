"""Re-export of the shared rate-limit decorator.

The implementation now lives once in ``origami_common.decorators``; this module
keeps the ``common.decorators.rate_limit`` import path working for the views
across tarmar-studio that already use it.
"""

from origami_common.decorators import rate_limit

__all__ = ["rate_limit"]
