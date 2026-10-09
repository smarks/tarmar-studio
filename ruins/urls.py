from django.urls import path

from .views import GenerateRuinView

app_name = "ruins"

urlpatterns = [
    path("", GenerateRuinView.as_view(), name="generate"),
]
