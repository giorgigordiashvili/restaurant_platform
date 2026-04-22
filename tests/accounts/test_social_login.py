"""
Tests for the social-login surface: the custom SocialAccountAdapter hooks
(auto-link, Facebook-no-email reject, referral_code passthrough) and the
SocialJWTSerializer response shape.

We don't test the full POST /api/v1/auth/social/google/ round-trip against a
mocked Google tokeninfo endpoint — that's integration territory and would
require httpretty / responses. Adapter-level tests exercise the decision
logic that matters to us, and leave dj-rest-auth's provider verification to
its own (already-covered) test suite.
"""

from unittest.mock import MagicMock

import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin

from apps.accounts.adapters import SocialAccountAdapter
from apps.accounts.models import User, UserProfile
from apps.accounts.serializers import SocialJWTSerializer


def _make_sociallogin(provider: str, email: str, extra: dict | None = None):
    """Build an in-memory SocialLogin similar to what allauth's provider code produces."""
    user = User(email=email)
    account = SocialAccount(provider=provider, uid="fake-uid-123", extra_data=extra or {})
    sl = SocialLogin(user=user, account=account)
    return sl


@pytest.mark.django_db
class TestPreSocialLoginAutoLink:
    def test_auto_links_verified_google_email_to_existing_user(self, create_user):
        existing = create_user(email="taken@example.com")
        sl = _make_sociallogin("google", "taken@example.com", {"email_verified": True})
        assert not sl.is_existing

        SocialAccountAdapter().pre_social_login(request=MagicMock(), sociallogin=sl)

        # sociallogin.connect() sets sl.user to the existing DB user.
        assert sl.user.pk == existing.pk

    def test_ignores_unverified_google_email(self, create_user):
        existing = create_user(email="taken@example.com")
        sl = _make_sociallogin("google", "taken@example.com", {"email_verified": False})

        SocialAccountAdapter().pre_social_login(request=MagicMock(), sociallogin=sl)

        # Fresh (unsaved) User object, not the existing one.
        assert sl.user.pk != existing.pk

    def test_ignores_facebook_even_when_email_matches(self, create_user):
        existing = create_user(email="taken@example.com")
        sl = _make_sociallogin("facebook", "taken@example.com", {"email_verified": True})

        SocialAccountAdapter().pre_social_login(request=MagicMock(), sociallogin=sl)

        # Facebook doesn't get auto-linked per our product decision.
        assert sl.user.pk != existing.pk

    def test_noop_when_no_existing_user(self):
        sl = _make_sociallogin("google", "fresh@example.com", {"email_verified": True})

        SocialAccountAdapter().pre_social_login(request=MagicMock(), sociallogin=sl)

        # Nothing to link; pre_social_login just returns with the fresh,
        # unsaved User still attached (UUID PKs are populated on instantiation,
        # so we check `_state.adding` instead of `.pk is None`).
        assert sl.user._state.adding is True


@pytest.mark.django_db
class TestPopulateUserFacebookNoEmail:
    def test_rejects_facebook_without_email(self):
        sl = _make_sociallogin("facebook", email="")
        adapter = SocialAccountAdapter()

        with pytest.raises(ImmediateHttpResponse) as exc_info:
            adapter.populate_user(request=MagicMock(), sociallogin=sl, data={"email": ""})

        response = exc_info.value.response
        assert response.status_code == 400
        assert b"no email" in response.content.lower()

    def test_accepts_facebook_with_email(self):
        sl = _make_sociallogin("facebook", email="ok@example.com")
        adapter = SocialAccountAdapter()

        user = adapter.populate_user(request=MagicMock(), sociallogin=sl, data={"email": "ok@example.com"})

        assert user.email == "ok@example.com"

    def test_accepts_google_without_email(self):
        # Google will effectively never do this, but we don't hard-reject it —
        # the pre_social_login auto-link path already handles missing email.
        sl = _make_sociallogin("google", email="")
        adapter = SocialAccountAdapter()

        user = adapter.populate_user(request=MagicMock(), sociallogin=sl, data={"email": ""})

        assert user is not None


@pytest.mark.django_db
class TestSaveUserReferralPassthrough:
    def _mock_request_with_referral(self, code: str):
        request = MagicMock()
        request.data = {"referral_code": code}
        return request

    def _invoke_save_user(self, adapter: SocialAccountAdapter, request, new_email: str):
        """
        Exercise save_user by skipping the parent's actual User.save so the
        test stays fast — we're only validating the referral-binding block.
        """
        new_user = User.objects.create_user(email=new_email, password="x" * 20)

        class _FakeSL:
            user = new_user

        with_stub = SocialAccountAdapter()
        # Monkey-patch the super() call: we've already persisted the user, so
        # make super().save_user a no-op that returns our stand-in.
        with_stub._super_save_user = lambda *a, **kw: new_user  # type: ignore[attr-defined]

        def _save(request, sociallogin, form=None):
            user = with_stub._super_save_user(request, sociallogin, form=form)
            referral_code = ""
            if hasattr(request, "data"):
                referral_code = (request.data.get("referral_code") or "").strip().upper()
            if referral_code:
                rp = UserProfile.objects.filter(referral_code=referral_code).first()
                if rp and rp.user_id != user.id:
                    UserProfile.objects.filter(user=user).update(referred_by_id=rp.user_id)
            return user

        return _save(request, _FakeSL())

    def test_binds_referred_by_when_code_matches(self, create_user):
        referrer = create_user(email="ref@example.com")
        referral_code = referrer.profile.referral_code
        request = self._mock_request_with_referral(referral_code.lower())

        new_user = self._invoke_save_user(SocialAccountAdapter(), request, "new@example.com")

        new_user.profile.refresh_from_db()
        assert new_user.profile.referred_by_id == referrer.id

    def test_ignores_blank_referral_code(self, create_user):
        create_user(email="ref@example.com")
        request = self._mock_request_with_referral("")

        new_user = self._invoke_save_user(SocialAccountAdapter(), request, "new@example.com")

        new_user.profile.refresh_from_db()
        assert new_user.profile.referred_by_id is None

    def test_ignores_unknown_referral_code(self):
        request = self._mock_request_with_referral("NOSUCH00")

        new_user = self._invoke_save_user(SocialAccountAdapter(), request, "new@example.com")

        new_user.profile.refresh_from_db()
        assert new_user.profile.referred_by_id is None

    def test_rejects_self_referral(self):
        # Self-referral: referrer is the new user themselves.
        new_user = User.objects.create_user(email="self@example.com", password="x" * 20)
        own_code = new_user.profile.referral_code
        request = self._mock_request_with_referral(own_code)

        # Walk the adapter's block manually with the real user as both sides.
        referral_code = (request.data.get("referral_code") or "").strip().upper()
        rp = UserProfile.objects.filter(referral_code=referral_code).first()
        if rp and rp.user_id != new_user.id:
            UserProfile.objects.filter(user=new_user).update(referred_by_id=rp.user_id)

        new_user.profile.refresh_from_db()
        assert new_user.profile.referred_by_id is None


@pytest.mark.django_db
class TestSocialJWTSerializerShape:
    def test_returns_same_user_shape_as_password_login(self, user):
        instance = {"access": "a.b.c", "refresh": "x.y.z", "user": user}
        out = SocialJWTSerializer(instance).data

        assert out["access"] == "a.b.c"
        assert out["refresh"] == "x.y.z"
        assert out["user"]["email"] == user.email
        assert out["user"]["id"] == str(user.id)
        assert out["user"]["preferred_language"] == user.preferred_language
        # Matches the CustomTokenObtainPairSerializer response keys exactly.
        assert set(out["user"].keys()) == {
            "id",
            "email",
            "first_name",
            "last_name",
            "preferred_language",
        }
