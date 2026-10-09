from django.urls import path

from . import views
from .views import ReferenceView

app_name = "reference"

urlpatterns = [
    path("search/", views.search, name="search"),
    path("", ReferenceView.as_view(), name="index"),
    path("<path:path>/", ReferenceView.as_view(), name="view"),
]
