"""URLs for the adventure-hook generator (issue #276).

A second URL module in the ruins app — the generators' shared home — so the
page mounts at ``/tarmar/gm/hooks/`` without implying hooks live inside a
ruin.
"""

from django.urls import path

from .views import GenerateHookView, HookPartyPickerView

app_name = "hooks"

urlpatterns = [
    path("", GenerateHookView.as_view(), name="generate"),
    # The party picker's search (#864).
    path("parties/", HookPartyPickerView.as_view(), name="party_picker"),
]
