from django.urls import path

from .views import (
    EmailChangeView,
    EmailConfirmView,
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
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
    # Password reset (issue #929). One prefix, so common.public_paths can
    # take the whole flow out of the passphrase gate in one line.
    path("password_reset/", PasswordResetView.as_view(), name="password_reset"),
    path(
        "password_reset/done/",
        PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "password_reset/complete/",
        PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # The email-confirmation link (issue #931) sits under the reset prefix on
    # purpose: it needs to be outside the passphrase gate for the same reason
    # the reset link does, and this keeps the gate's exemptions to one prefix.
    # Three segments, so it never matches the reset confirm route's two.
    path(
        "password_reset/address/<uidb64>/<token>/",
        EmailConfirmView.as_view(),
        name="email_confirm",
    ),
    path(
        "password_reset/<uidb64>/<token>/",
        PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/email/", EmailChangeView.as_view(), name="email_change"),
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
