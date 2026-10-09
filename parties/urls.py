from django.urls import path

from .views import (
    PartyCreateView,
    PartyDetailView,
    PartyEditView,
    PartyListView,
    party_add_member,
    party_disband,
    party_remove_member,
    party_restore,
    party_update_field,
)

app_name = "parties"

urlpatterns = [
    path("", PartyListView.as_view(), name="list"),
    path("create/", PartyCreateView.as_view(), name="create"),
    path("<int:pk>/", PartyDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", PartyEditView.as_view(), name="edit"),
    path("<int:pk>/add-member/", party_add_member, name="add_member"),
    path("<int:pk>/remove-member/", party_remove_member, name="remove_member"),
    path("<int:pk>/update-field/", party_update_field, name="update_field"),
    path("<int:pk>/disband/", party_disband, name="disband"),
    path("<int:pk>/restore/", party_restore, name="restore"),
]
