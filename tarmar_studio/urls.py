"""URL configuration for tarmar_studio project."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView

from accounts.views import AboutView, FrontDoorView, HomeView, JournalView


def health_check(request):
    """Simple health check endpoint for blue-green deployment."""
    from django.db import connection

    # Verify database connectivity
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health_check, name="health"),
    # Host-aware: the studio home on the default host, the battle list on a
    # classic-arena host (issue #269). See accounts.views.FrontDoorView.
    path("", FrontDoorView.as_view(), name="landing"),
    path("admin/", admin.site.urls),
    # All Tarmar routes under /tarmar/ prefix
    path("tarmar/", HomeView.as_view(), name="home"),
    path("tarmar/about/", AboutView.as_view(), name="about"),
    # The signed-in player's journal, off the front page since #924 and
    # named the journal since #952, where a sign-in on the default host lands.
    path("tarmar/journal/", JournalView.as_view(), name="journal"),
    # Its address before #952, kept so a bookmark still finds it.
    path(
        "tarmar/table/",
        RedirectView.as_view(pattern_name="journal", permanent=True),
    ),
    path("tarmar/accounts/", include("accounts.urls")),
    path("tarmar/characters/", include("characters.urls")),
    path("tarmar/parties/", include("parties.urls")),
    path("tarmar/reference/", include("reference.urls")),
    path("tarmar/export/", include("character_export.urls")),
    path("tarmar/gm/bestiary/", include("bestiary.urls")),
    path("tarmar/gm/encounters/", include("encounters.urls")),
    path("tarmar/gm/ruins/", include("ruins.urls")),
    path("tarmar/gm/hooks/", include("ruins.hook_urls")),
    # The other GM-tools table pages, rolled table by table (issue #809).
    path("tarmar/gm/tools/", include("ruins.table_urls")),
    path("tarmar/gm/", include("gm.urls")),
    path("tarmar/battles/", include("battle.urls")),
]
