"""URLs for the adventure-hook generator (issue #276).

A second URL module in the ruins app — the generators' shared home — so the
page mounts at ``/tarmar/gm/hooks/`` without implying hooks live inside a
ruin.
"""

from django.urls import path

from .views import GenerateHookView

app_name = "hooks"

urlpatterns = [
    path("", GenerateHookView.as_view(), name="generate"),
]
