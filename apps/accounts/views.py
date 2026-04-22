"""
Views for the accounts app.
"""

import base64
import hashlib
import hmac
import json
import logging
import uuid

from django.conf import settings

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.facebook.views import FacebookOAuth2Adapter
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from dj_rest_auth.registration.views import SocialLoginView
from drf_spectacular.utils import extend_schema
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.throttling import AuthRateThrottle, PasswordResetThrottle

from .models import User
from .serializers import (
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    SocialLoginSerializer,
    UserRegistrationSerializer,
    UserSerializer,
    UserUpdateSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["Auth"])
class RegisterView(generics.CreateAPIView):
    """
    Register a new user account.
    """

    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Generate tokens
        refresh = RefreshToken.for_user(user)

        # Set audit action
        request._audit_action = "user_create"
        request._audit_target_model = "User"
        request._audit_target_id = str(user.id)

        return Response(
            {
                "success": True,
                "message": "Registration successful",
                "data": {
                    "user": UserSerializer(user).data,
                    "tokens": {
                        "refresh": str(refresh),
                        "access": str(refresh.access_token),
                    },
                },
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Auth"])
class LoginView(TokenObtainPairView):
    """
    Authenticate user and return JWT tokens.
    """

    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [AuthRateThrottle]

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            # Set audit action
            request._audit_action = "login"
            request._audit_description = f"User logged in: {request.data.get('email')}"

            # Update last login IP
            try:
                user = User.objects.get(email__iexact=request.data.get("email"))
                ip = self._get_client_ip(request)
                user.update_last_login_ip(ip)
            except User.DoesNotExist:
                pass

        return response

    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


@extend_schema(tags=["Auth"])
class LogoutView(APIView):
    """
    Logout user by blacklisting the refresh token.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()

            # Set audit action
            request._audit_action = "logout"

            return Response({"success": True, "message": "Successfully logged out"}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"success": False, "error": {"message": "Failed to logout", "details": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )


@extend_schema(tags=["Auth"])
class TokenRefreshView(TokenRefreshView):
    """
    Refresh access token using refresh token.
    """

    throttle_classes = [AuthRateThrottle]


@extend_schema(tags=["Auth"])
class PasswordResetRequestView(APIView):
    """
    Request password reset email.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetThrottle]
    serializer_class = PasswordResetRequestSerializer

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]

        # Always return success to prevent email enumeration
        # In production, send actual reset email here
        try:
            user = User.objects.get(email__iexact=email)
            # TODO: Send password reset email
            # send_password_reset_email.delay(user.id)

            request._audit_action = "password_reset"
            request._audit_target_model = "User"
            request._audit_target_id = str(user.id)
        except User.DoesNotExist:
            pass

        return Response(
            {"success": True, "message": "If an account with this email exists, a password reset link has been sent."},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Auth"])
class PasswordResetConfirmView(APIView):
    """
    Confirm password reset with token.
    """

    permission_classes = [AllowAny]
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # TODO: Implement actual token validation
        # For now, return a placeholder response

        return Response({"success": True, "message": "Password has been reset successfully"}, status=status.HTTP_200_OK)


@extend_schema(tags=["Auth"])
class ChangePasswordView(APIView):
    """
    Change password for authenticated user.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Set audit action
        request._audit_action = "password_change"
        request._audit_target_model = "User"
        request._audit_target_id = str(request.user.id)

        return Response({"success": True, "message": "Password changed successfully"}, status=status.HTTP_200_OK)


# User Profile Views


@extend_schema(tags=["Users"])
class CurrentUserView(generics.RetrieveUpdateAPIView):
    """
    Get or update the current authenticated user's profile.
    """

    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method in ["PUT", "PATCH"]:
            return UserUpdateSerializer
        return UserSerializer

    def get_object(self):
        return self.request.user

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return Response({"success": True, "data": serializer.data})

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        # Set audit action
        request._audit_action = "user_update"
        request._audit_target_model = "User"
        request._audit_target_id = str(request.user.id)

        return Response(
            {"success": True, "message": "Profile updated successfully", "data": UserSerializer(instance).data}
        )


@extend_schema(tags=["Users"])
class DeleteAccountView(APIView):
    """
    Delete the current user's account.
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request):
        user = request.user

        # Set audit action before deletion
        request._audit_action = "user_delete"
        request._audit_target_model = "User"
        request._audit_target_id = str(user.id)
        request._audit_description = f"User deleted account: {user.email}"

        # Soft delete or hard delete based on requirements
        user.is_active = False
        user.save()

        return Response({"success": True, "message": "Account has been deactivated"}, status=status.HTTP_200_OK)


# ── Social login ──────────────────────────────────────────────────────
#
# The frontend (GoogleOAuthProvider / Facebook JS SDK) does the OAuth dance
# and hands us an access_token. allauth's provider adapters verify that
# token with the provider, which gives us the user's email + profile. Our
# custom SocialAccountAdapter (apps.accounts.adapters) then auto-links on
# verified-email match, rejects Facebook-without-email, and applies the
# optional referral_code the frontend posted. ``client_class=None`` keeps
# dj-rest-auth in the access-token flow instead of trying to do a
# code-exchange with an OAuth client secret it doesn't need.


@extend_schema(tags=["Auth"])
class GoogleSocialLoginView(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    serializer_class = SocialLoginSerializer
    client_class = None
    throttle_classes = [AuthRateThrottle]


@extend_schema(tags=["Auth"])
class FacebookSocialLoginView(SocialLoginView):
    adapter_class = FacebookOAuth2Adapter
    serializer_class = SocialLoginSerializer
    client_class = None
    throttle_classes = [AuthRateThrottle]


# ── Facebook data-deletion callback ────────────────────────────────────
#
# Facebook's Data Deletion Callback is the endpoint Meta requires before an
# app with Facebook Login can go Live. When a user requests deletion from
# their Facebook Settings, Meta POSTs a `signed_request` to this URL. We:
#
#   1. Split the signed_request on "." → [base64url-encoded HMAC, payload].
#   2. Verify the HMAC using FACEBOOK_APP_SECRET so we know Facebook sent
#      it and nobody is spoofing a deletion for another user.
#   3. Parse the payload JSON → contains user_id (the Facebook uid).
#   4. Delete the SocialAccount row that links that Facebook user to our
#      User record — unlinks the Facebook login. The underlying User
#      account keeps its email/password/orders/reservations because those
#      are first-party data owned by Telos LLC, not by Facebook. Full
#      account deletion is a separate self-service flow via /profile.
#   5. Return JSON { url, confirmation_code } per Meta's spec — the user
#      visits that URL to check their deletion status.
#
# Spec: https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback/


def _parse_fb_signed_request(signed_request: str, app_secret: str) -> dict | None:
    """Return the decoded payload dict, or None if HMAC fails or the body is malformed."""
    if not signed_request or "." not in signed_request:
        return None
    try:
        encoded_sig, encoded_payload = signed_request.split(".", 1)
    except ValueError:
        return None

    # base64url decode — pad with '=' so Python's decoder doesn't reject it.
    def _b64url(s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    try:
        sig = _b64url(encoded_sig)
        payload_bytes = _b64url(encoded_payload)
    except Exception:
        return None

    expected = hmac.new(app_secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        return json.loads(payload_bytes)
    except json.JSONDecodeError:
        return None


@extend_schema(tags=["Auth"])
class FacebookDataDeletionView(APIView):
    """
    Meta's data-deletion-callback endpoint. POST only, public (Facebook
    authenticates itself via the HMAC-signed payload, not with a bearer token).
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []  # no CSRF / JWT — signed_request IS the auth.

    def post(self, request, *args, **kwargs):
        signed_request = request.data.get("signed_request") or request.POST.get("signed_request") or ""
        app_secret = getattr(settings, "FACEBOOK_APP_SECRET", "")
        if not app_secret:
            logger.warning("Facebook data-deletion called but FACEBOOK_APP_SECRET is not configured.")
            return Response({"error": "facebook_not_configured"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        payload = _parse_fb_signed_request(signed_request, app_secret)
        if payload is None:
            return Response({"error": "invalid_signed_request"}, status=status.HTTP_400_BAD_REQUEST)

        fb_user_id = str(payload.get("user_id") or "").strip()
        if not fb_user_id:
            return Response({"error": "missing_user_id"}, status=status.HTTP_400_BAD_REQUEST)

        # Unlink the Facebook social account. Keep the underlying User so the
        # customer still has their orders, loyalty, and referral code — if
        # they want a full account delete they can do it from /profile.
        deleted_count, _ = SocialAccount.objects.filter(provider="facebook", uid=fb_user_id).delete()
        logger.info(
            "Facebook data-deletion callback: removed %s SocialAccount row(s) for uid=%s", deleted_count, fb_user_id
        )

        confirmation_code = uuid.uuid4().hex
        base_url = getattr(settings, "FRONTEND_BASE_URL", "https://aimenu.ge").rstrip("/")
        status_url = f"{base_url}/data-deletion?code={confirmation_code}"
        return Response(
            {"url": status_url, "confirmation_code": confirmation_code},
            status=status.HTTP_200_OK,
        )
