"""Re-export of the shared audit logger.

The logger is defined once in ``origami_common.audit`` (logger name
``"origami.audit"`` — see the matching entry in ``settings.LOGGING``). Re-exported
here so existing ``common.audit.audit_logger`` imports keep working.
"""

from origami_common.audit import audit_logger

__all__ = ["audit_logger"]
