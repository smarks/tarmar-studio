"""URL configuration for character export app."""

from django.urls import path

from . import views

app_name = "character_export"

urlpatterns = [
    path(
        "<int:character_id>/markdown/",
        views.export_markdown,
        name="export_markdown",
    ),
    path(
        "<int:character_id>/json/",
        views.export_json,
        name="export_json",
    ),
    path(
        "<int:character_id>/pdf/",
        views.export_pdf,
        name="export_pdf",
    ),
]
