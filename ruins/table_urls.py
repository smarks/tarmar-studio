"""The GM-tools table roller's route (issue #809).

Mounted at ``/tarmar/gm/tools/`` so a page's roller lives at
``/tarmar/gm/tools/<page>/roll/?seed=N``; the view serves only the pages in
:data:`ruins.table_rolls.TABLE_PAGE_SLUGS`.
"""

from django.urls import path

from .views import RollTablePageView

app_name = "table_rolls"

urlpatterns = [
    path("<slug:page>/roll/", RollTablePageView.as_view(), name="roll"),
]
