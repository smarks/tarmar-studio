"""URL configuration for tarmar_studio project."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from accounts.views import AboutView, FrontDoorView, HomeView


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
    path("tarmar/accounts/", include("accounts.urls")),
    path("tarmar/characters/", include("characters.urls")),
    path("tarmar/parties/", include("parties.urls")),
    path("tarmar/reference/", include("reference.urls")),
    path("tarmar/export/", include("character_export.urls")),
    path("tarmar/gm/bestiary/", include("bestiary.urls")),
    path("tarmar/gm/encounters/", include("encounters.urls")),
    path("tarmar/gm/ruins/", include("ruins.urls")),
    path("tarmar/gm/hooks/", include("ruins.hook_urls")),
    path("tarmar/gm/", include("gm.urls")),
    path("tarmar/battles/", include("battle.urls")),
]
