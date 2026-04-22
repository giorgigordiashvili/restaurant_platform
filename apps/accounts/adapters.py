"""
Allauth adapters for our JWT-backed social login flow.

django-allauth invokes a handful of hooks during a social sign-in: we subclass
the defaults to implement three product decisions pinned with the user:

1. Auto-link a verified Google social account onto an existing User. Someone
   who registered with password + email in the past and now clicks "Continue
   with Google" should land in the same account, not a sibling one.

2. Reject Facebook sign-ins that come back without an email. Our User model
   has a ``unique NOT NULL`` constraint on ``email``; rather than synthesize
   ``fb_<id>@aimenu.ge`` ghosts we surface a clear 400 and let the customer
   sign up with email instead.

3. Apply the optional ``referral_code`` the frontend posts alongside the
   provider token, matching what the password-signup serializer does.
"""

from __future__ import annotations

from django.http import JsonResponse

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from apps.accounts.models import User, UserProfile


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        """
        Attach a fresh Google sign-in to an existing User if the Google account
        reports the email as verified. Only fires for Google — Facebook's
        ``verified`` signal is unreliable so we don't auto-link there.
        """
        if sociallogin.is_existing:
            return
        email = (sociallogin.user.email or "").lower().strip()
        if not email:
            return
        if sociallogin.account.provider != "google":
            return
        # Google's tokeninfo response includes ``email_verified``; the legacy
        # userinfo endpoint uses ``verified_email``. Accept either.
        extra = sociallogin.account.extra_data or {}
        email_verified = extra.get("email_verified") or extra.get("verified_email")
        if not email_verified:
            return
        try:
            existing = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return
        sociallogin.connect(request, existing)

    def populate_user(self, request, sociallogin, data):
        """
        Hard-reject Facebook sign-ins that come back without an email. Raising
        ``ImmediateHttpResponse`` short-circuits allauth's pipeline with our
        400 payload so dj-rest-auth returns the message verbatim.
        """
        user = super().populate_user(request, sociallogin, data)
        if sociallogin.account.provider == "facebook" and not (user.email or "").strip():
            raise ImmediateHttpResponse(
                JsonResponse(
                    {"detail": ("Your Facebook account has no email — please sign up " "with email instead.")},
                    status=400,
                )
            )
        return user

    def save_user(self, request, sociallogin, form=None):
        """
        Mirror the email-password signup serializer: if the client posted a
        ``referral_code`` alongside the provider token, resolve it against the
        existing UserProfile index and bind the new user's ``referred_by``.
        Unknown codes are silently ignored — we validated up-front that they
        exist in the SocialLoginSerializer. Self-referrals are rejected.
        """
        user = super().save_user(request, sociallogin, form=form)
        referral_code = ""
        if hasattr(request, "data"):
            referral_code = (request.data.get("referral_code") or "").strip().upper()
        if referral_code:
            referrer_profile = UserProfile.objects.filter(referral_code=referral_code).first()
            if referrer_profile and referrer_profile.user_id != user.id:
                UserProfile.objects.filter(user=user).update(referred_by_id=referrer_profile.user_id)
        return user
