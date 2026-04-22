"""Customer-facing referral endpoint tests."""

from decimal import Decimal

import pytest

from apps.referrals.services import manual_adjustment


@pytest.mark.django_db
class TestReferralSummary:
    def test_returns_code_and_balance(self, authenticated_client, user, settings):
        settings.REFERRAL_DEFAULT_PERCENT = "0.5"
        manual_adjustment(user=user, amount=Decimal("4.20"), created_by=user)
        response = authenticated_client.get("/api/v1/referrals/me/")
        assert response.status_code == 200
        body = response.json()
        assert body["referral_code"]
        assert body["wallet_balance"] == "4.20"
        assert body["effective_percent"] == "0.50"
        assert body["referred_users_count"] == 0
        assert body["referral_url"].endswith(f"/register?ref={body['referral_code']}")

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/v1/referrals/me/").status_code == 401


@pytest.mark.django_db
class TestWalletHistory:
    def test_lists_user_transactions(self, authenticated_client, user):
        manual_adjustment(user=user, amount=Decimal("5"), created_by=user, notes="welcome")
        manual_adjustment(user=user, amount=Decimal("-1"), created_by=user, notes="trim")
        response = authenticated_client.get("/api/v1/referrals/history/")
        assert response.status_code == 200
        body = response.json()
        rows = body["results"] if isinstance(body, dict) and "results" in body else body
        assert len(rows) == 2
        assert {r["amount"] for r in rows} == {"5.00", "-1.00"}


@pytest.mark.django_db
class TestReferredUsersList:
    def test_includes_only_my_referrals(self, authenticated_client, user, another_user):
        another_user.profile.referred_by = user
        another_user.profile.save(update_fields=["referred_by", "updated_at"])
        response = authenticated_client.get("/api/v1/referrals/referred/")
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["email"] == another_user.email


@pytest.mark.django_db
class TestRegistrationReferralCode:
    def test_register_with_valid_code_links_referrer(self, api_client, user, user_data):
        code = user.profile.referral_code
        payload = {**user_data, "email": "newuser@example.com", "referral_code": code}
        response = api_client.post("/api/v1/auth/register/", data=payload, format="json")
        assert response.status_code in (200, 201)
        from apps.accounts.models import User

        new_user = User.objects.get(email="newuser@example.com")
        assert new_user.profile.referred_by_id == user.id

    def test_register_with_invalid_code_400s(self, api_client, user_data):
        payload = {**user_data, "email": "another@example.com", "referral_code": "NOSUCH00"}
        response = api_client.post("/api/v1/auth/register/", data=payload, format="json")
        assert response.status_code == 400
