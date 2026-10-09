from django.urls import path

from .views import (
    LoginView,
    LogoutView,
    ProfileView,
    RegisterView,
    UsersListView,
    delete_user,
    profile_update_field,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/<int:user_id>/", ProfileView.as_view(), name="user_profile"),
    path("profile/update-field/", profile_update_field, name="profile_update_field"),
    path(
        "profile/<int:user_id>/update-field/",
        profile_update_field,
        name="user_profile_update_field",
    ),
    path("users/", UsersListView.as_view(), name="users_list"),
    path("users/<int:user_id>/delete/", delete_user, name="delete_user"),
]
