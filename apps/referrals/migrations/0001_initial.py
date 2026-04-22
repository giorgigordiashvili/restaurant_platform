# Initial migration for the referrals app — WalletTransaction ledger.
# Hand-written so the dependencies don't drift onto noise from other apps
# (per the CLAUDE.md "manage.py makemigrations cross-app drift" gotcha).

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0002_userprofile_referral_wallet"),
        ("orders", "0006_order_wallet_applied"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WalletTransaction",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("referral_credit", "Referral credit"),
                            ("order_spend", "Order spend"),
                            ("refund_credit", "Refund credit"),
                            ("referral_clawback", "Referral clawback"),
                            ("manual_adjustment", "Manual adjustment"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Signed: positive for credit, negative for debit.",
                        max_digits=10,
                    ),
                ),
                (
                    "balance_after",
                    models.DecimalField(
                        decimal_places=2,
                        help_text=(
                            "Snapshot of user.profile.wallet_balance immediately after this row was written."
                        ),
                        max_digits=10,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                (
                    "user",
                    models.ForeignKey(
                        help_text="Owner of the wallet being credited / debited.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="wallet_transactions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_order",
                    models.ForeignKey(
                        blank=True,
                        help_text="Order this credit / debit relates to (if any).",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="wallet_transactions",
                        to="orders.order",
                    ),
                ),
                (
                    "referred_user",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "For referral_credit / referral_clawback: the referred user "
                            "whose order triggered this row."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="referral_credits_triggered",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Admin who created this row, for manual_adjustment kinds.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="wallet_transactions_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Wallet transaction",
                "verbose_name_plural": "Wallet transactions",
                "db_table": "wallet_transactions",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="wallettransaction",
            index=models.Index(fields=["user", "-created_at"], name="wallet_user_created_idx"),
        ),
        migrations.AddIndex(
            model_name="wallettransaction",
            index=models.Index(fields=["kind"], name="wallet_kind_idx"),
        ),
    ]
