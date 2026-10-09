from django.urls import path

from .views import EncounterGeneratorView

app_name = "encounters"

urlpatterns = [
    path("", EncounterGeneratorView.as_view(), name="generator"),
]
