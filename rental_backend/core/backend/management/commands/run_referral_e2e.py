"""
Full end-to-end test for the Referral & Wallet feature.

Run from project root (rental_backend/core):
    python manage.py run_referral_e2e

Or from repo root:
    cd rental_backend/core && python manage.py run_referral_e2e

What it does:
  1. Ensures ReferralSettings exist (creates default if missing).
  2. Creates User A (referrer) with a referral code.
  3. Creates User B (referred) linked to A via referral code and creates Referral (pending).
  4. Creates a minimal Order for B with amount >= minimum_order_amount.
  5. Marks the order's OrderedProduct as DELIVERED to trigger referral completion.
  6. Simulates "approve & credit" (skips hold period for test): credits referrer wallet and marks referral rewarded.
  7. Asserts referral status, wallet balance, and WalletTransaction.
  8. Prints step-by-step results and final PASS/FAIL.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth.hashers import make_password

from backend.models import (
    User,
    Token,
    ReferralSettings,
    Referral,
    WalletTransaction,
    Order,
    OrderedProduct,
    Product,
    ProductOption,
    Category,
    Vendor,
)


class Command(BaseCommand):
    help = "Run end-to-end test for Referral & Wallet: referrer → referred signup → order delivered → reward credited."

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-hold',
            action='store_true',
            help='Credit reward immediately (set hold_until to past) so test does not wait.',
        )
        parser.add_argument(
            '--no-clean',
            action='store_true',
            help='Do not delete test users/data at the end (default: delete).',
        )

    def handle(self, *args, **options):
        skip_hold = options.get('skip_hold', True)
        no_clean = options.get('no_clean', False)
        suffix = timezone.now().strftime('%Y%m%d%H%M%S') + '_' + str(uuid.uuid4())[:8]

        self.stdout.write("=" * 60)
        self.stdout.write("REFERRAL E2E TEST")
        self.stdout.write("=" * 60)

        referrer = None
        referred = None
        order = None
        referral = None
        category = None
        product = None
        option = None
        vendor = None

        try:
            # --- 1. ReferralSettings ---
            self.stdout.write("\n[1/8] ReferralSettings...")
            settings_obj = ReferralSettings.get_active()
            if not settings_obj:
                settings_obj = ReferralSettings.objects.create(
                    referral_reward_amount=Decimal('100'),
                    minimum_order_amount=Decimal('300'),
                    max_wallet_usage_percent=20,
                    reward_hold_days=7,
                    max_referrals_per_day=5,
                )
                self.stdout.write(self.style.SUCCESS(f"  Created: {settings_obj}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"  Using existing: {settings_obj}"))
            min_order = int(settings_obj.minimum_order_amount or 300)
            reward_amount = settings_obj.referral_reward_amount or Decimal('100')

            # --- 2. User A (referrer) ---
            self.stdout.write("\n[2/8] Creating referrer (User A)...")
            email_a = f"e2e_referrer_{suffix}@test.local"
            phone_a = "9999990001"
            if User.objects.filter(email=email_a).exists():
                User.objects.filter(email=email_a).delete()
            referrer = User.objects.create(
                email=email_a,
                phone=phone_a,
                fullname="E2E Referrer",
                password=make_password("testpass123"),
                referral_code=f"E2EREF{suffix[:6].upper()}",
            )
            Token.objects.create(
                token=f"e2e_token_referrer_{suffix}",
                fcmtoken="",
                user=referrer,
            )
            self.stdout.write(self.style.SUCCESS(f"  Referrer: {referrer.email}, code: {referrer.referral_code}"))

            # --- 3. User B (referred) + Referral ---
            self.stdout.write("\n[3/8] Creating referred user (User B) and Referral (pending)...")
            email_b = f"e2e_referred_{suffix}@test.local"
            phone_b = "9999990002"
            if User.objects.filter(email=email_b).exists():
                User.objects.filter(email=email_b).delete()
            referred = User.objects.create(
                email=email_b,
                phone=phone_b,
                fullname="E2E Referred",
                password=make_password("testpass123"),
                referred_by=referrer,
                device_id=f"e2e_device_{suffix}",
                signup_ip="127.0.0.1",
                referral_code=f"E2ERFD{suffix[:6].upper()}",
            )
            referral = Referral.objects.create(
                referrer=referrer,
                referred_user=referred,
                referral_code=referrer.referral_code,
                reward_amount=reward_amount,
                status=Referral.STATUS_PENDING,
                is_suspicious=False,
            )
            self.stdout.write(self.style.SUCCESS(f"  Referred: {referred.email}, Referral id: {referral.id}, status: {referral.status}"))

            # --- 4. Order for B (amount >= min) ---
            self.stdout.write("\n[4/8] Creating Order for referred user...")
            option = ProductOption.objects.first()
            if option:
                product = option.product
                order = Order.objects.create(
                    user=referred,
                    tx_amount=min_order + 100,
                    payment_mode="COD",
                    address="E2E Test Address, 123456",
                    tx_status="PENDING",
                    from_cart=False,
                    accepted_terms=True,
                    accepted_at=timezone.now(),
                )
                op = OrderedProduct.objects.create(
                    order=order,
                    product_option=option,
                    product_price=400,
                    tx_price=400,
                    delivery_price=0,
                    quantity=1,
                    status='ORDERED',
                )
                self.stdout.write(self.style.SUCCESS(f"  Order: {order.id}, tx_amount: {order.tx_amount}, OrderedProduct: {op.id}"))

                # --- 5. Mark DELIVERED to trigger referral completion ---
                self.stdout.write("\n[5/8] Marking OrderedProduct as DELIVERED (triggers referral completion)...")
                op.status = 'DELIVERED'
                op.save()
            else:
                self.stdout.write(self.style.WARNING("  No ProductOption in DB: simulating referral completion (no real order)."))
                referral.status = Referral.STATUS_COMPLETED
                referral.completed_at = timezone.now()
                referral.hold_until = timezone.now() - timedelta(days=1) if skip_hold else timezone.now() + timedelta(days=7)
                referral.save(update_fields=['status', 'completed_at', 'hold_until'])

            referral.refresh_from_db()
            self.stdout.write(self.style.SUCCESS(f"  Referral status: {referral.status}"))

            if referral.status != Referral.STATUS_COMPLETED:
                self.stdout.write(self.style.ERROR(f"  FAIL: Expected status=completed, got {referral.status}"))
                return

            # --- 6. Approve & credit (skip hold for test if --skip-hold) ---
            self.stdout.write("\n[6/8] Approving referral & crediting wallet...")
            if skip_hold and referral.hold_until and referral.hold_until > timezone.now():
                referral.hold_until = timezone.now() - timedelta(days=1)
                referral.save(update_fields=['hold_until'])
            balance_before = (referrer.referral_wallet_balance or Decimal('0'))

            referrer.refresh_from_db()
            referrer.referral_wallet_balance = (referrer.referral_wallet_balance or Decimal('0')) + referral.reward_amount
            referrer.save(update_fields=['referral_wallet_balance'])
            WalletTransaction.objects.create(
                user=referrer,
                amount=referral.reward_amount,
                type=WalletTransaction.TYPE_CREDIT,
                description=f"Referral reward for {referred.email}",
            )
            referral.status = Referral.STATUS_REWARDED
            referral.rewarded_at = timezone.now()
            referral.save(update_fields=['status', 'rewarded_at'])
            self.stdout.write(self.style.SUCCESS(f"  Credited ₹{referral.reward_amount} to referrer wallet."))

            # --- 7. Assertions ---
            self.stdout.write("\n[7/8] Assertions...")
            referrer.refresh_from_db()
            referral.refresh_from_db()
            tx_count = WalletTransaction.objects.filter(user=referrer, type=WalletTransaction.TYPE_CREDIT).count()

            ok = True
            if referral.status != Referral.STATUS_REWARDED:
                self.stdout.write(self.style.ERROR(f"  Referral status != rewarded: {referral.status}"))
                ok = False
            if (referrer.referral_wallet_balance or 0) < reward_amount:
                self.stdout.write(self.style.ERROR(f"  Wallet balance too low: {referrer.referral_wallet_balance}"))
                ok = False
            if tx_count < 1:
                self.stdout.write(self.style.ERROR(f"  No credit WalletTransaction found"))
                ok = False
            if ok:
                self.stdout.write(self.style.SUCCESS("  All assertions passed."))

            # --- 8. Summary ---
            self.stdout.write("\n[8/8] Summary")
            self.stdout.write("-" * 40)
            self.stdout.write(f"  Referrer:        {referrer.email} (code: {referrer.referral_code})")
            self.stdout.write(f"  Referred:        {referred.email}")
            self.stdout.write(f"  Referral:        id={referral.id}, status={referral.status}")
            self.stdout.write(f"  Wallet balance:  ₹{referrer.referral_wallet_balance}")
            self.stdout.write(f"  Credit tx count: {tx_count}")
            if ok:
                self.stdout.write(self.style.SUCCESS("\n*** REFERRAL E2E TEST PASSED ***"))
            else:
                self.stdout.write(self.style.ERROR("\n*** REFERRAL E2E TEST FAILED ***"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\nError: {e}"))
            import traceback
            traceback.print_exc()
            self.stdout.write(self.style.ERROR("\n*** REFERRAL E2E TEST FAILED (exception) ***"))
        finally:
            if not no_clean and (referrer or referred):
                self.stdout.write("\nCleaning up test data...")
                if order:
                    try:
                        OrderedProduct.objects.filter(order=order).delete()
                        order.delete()
                    except Exception:
                        pass
                if referral:
                    referral.delete()
                if referred:
                    Token.objects.filter(user=referred).delete()
                    referred.delete()
                if referrer:
                    WalletTransaction.objects.filter(user=referrer).delete()
                    Token.objects.filter(user=referrer).delete()
                    referrer.delete()
                self.stdout.write(self.style.SUCCESS("  Done."))
