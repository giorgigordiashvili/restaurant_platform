# Adds referral + wallet fields to UserProfile.
# Two-pass on referral_code so we can backfill existing rows before making it unique.

import secrets
import string
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

REFERRAL_CODE_LENGTH = 8
REFERRAL_CODE_ALPHABET = string.ascii_uppercase + string.digits


def backfill_referral_codes(apps, schema_editor):
    """Mint a unique 8-char code for every existing UserProfile row."""
    UserProfile = apps.get_model("accounts", "UserProfile")
    used = set(UserProfile.objects.exclude(referral_code="").values_list("referral_code", flat=True))
    for profile in UserProfile.objects.filter(referral_code=""):
        for _ in range(20):
            candidate = "".join(secrets.choice(REFERRAL_CODE_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH))
            if candidate not in used:
                used.add(candidate)
                profile.referral_code = candidate
                profile.save(update_fields=["referral_code", "updated_at"])
                break


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="referral_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Auto-generated code the user shares to refer others.",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="referred_by",
            field=models.ForeignKey(
                blank=True,
                help_text="The user whose referral_code was claimed at signup. Set once.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="referrals_made",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="wallet_balance",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="Spendable balance in GEL. Source-of-truth is the WalletTransaction ledger.",
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="referral_percent_override",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text=(
                    "Superuser-only. When set, overrides settings.REFERRAL_DEFAULT_PERCENT for "
                    "orders placed by users this profile has referred."
                ),
                max_digits=5,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("100")),
                ],
            ),
        ),
        migrations.RunPython(backfill_referral_codes, noop_reverse),
        migrations.AlterField(
            model_name="userprofile",
            name="referral_code",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Auto-generated code the user shares to refer others.",
                max_length=8,
                unique=True,
            ),
        ),
    ]
