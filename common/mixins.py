"""Re-export of the shared view mixins.

``ActivePageMixin`` is domain-agnostic (``origami_common``); ``AdminOrDMRequiredMixin``
depends on the user's ``can_manage_users`` role and lives with the auth layer
(``origami_auth``). Both are re-exported here so the existing
``common.mixins`` import path keeps working across the app.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from origami_auth.mixins import AdminOrDMRequiredMixin
from origami_common.mixins import ActivePageMixin

__all__ = ["ActivePageMixin", "AdminOrDMRequiredMixin", "DMToolsAccessMixin"]


class DMToolsAccessMixin(ActivePageMixin, LoginRequiredMixin, AdminOrDMRequiredMixin):
    """The gate on the DM tools: a logged-in DM or admin.

    The bestiary, encounter, ruin and hook generators each used to declare an
    identical copy of this (issue #337). Anonymous visitors are sent to login;
    a logged-in player is refused. Views set their own ``active_page``.
    """

    login_url = "login"
