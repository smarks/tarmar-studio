from django.apps import AppConfig

# The module, not the class: Django picks the ``accounts`` app's own config
# from every AppConfig subclass this module names, and an imported AdminConfig
# would be a second candidate with ``default = True``.
from django.contrib.admin import apps as django_admin_apps


class AccountsConfig(AppConfig):
    name = "accounts"


class LimitedAdminConfig(django_admin_apps.AdminConfig):
    """Django's admin app, serving ``accounts.admin_site.LimitedAdminSite``.

    ``AdminConfig.default_site`` is Django's hook for replacing the default
    ``AdminSite``; ``settings.INSTALLED_APPS`` lists this config in place of
    ``django.contrib.admin`` so the admin's sign-in is held to the site's
    login limiter (issue #173). ``name`` stays the admin's own, so the app
    label, templates and migrations are Django's as before.
    """

    # Not the ``accounts`` app's config: that is AccountsConfig above. This one
    # is named outright in INSTALLED_APPS, so it needs no default status.
    default = False
    default_site = "accounts.admin_site.LimitedAdminSite"
