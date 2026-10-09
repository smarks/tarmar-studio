from django.urls import path
from django.views.generic import RedirectView

from .views import (
    BeastDetailView,
    CatalogView,
    DeleteGeneratedBeastView,
    GenerateBeastView,
)

app_name = "bestiary"

urlpatterns = [
    path("", CatalogView.as_view(), name="catalog"),
    path("generate/", GenerateBeastView.as_view(), name="generate"),
    # The encounter generator moved to its own app (issue #218). Old seeded
    # result URLs are shared bookmarks, so this redirects permanently while
    # preserving the query string, and keeps the name "encounters" so any
    # stale {% url 'bestiary:encounters' %} still resolves.
    path(
        "encounters/",
        RedirectView.as_view(
            pattern_name="encounters:generator", permanent=True, query_string=True
        ),
        name="encounters",
    ),
    path("<slug:slug>/", BeastDetailView.as_view(), name="detail"),
    path("<slug:slug>/delete/", DeleteGeneratedBeastView.as_view(), name="delete"),
]
