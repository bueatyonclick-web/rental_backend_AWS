# Referral E2E Test

Full end-to-end script to verify the **Referral & Wallet** feature.

## Quick run

From **rental_backend/core** (where `manage.py` lives):

```bash
python manage.py run_referral_e2e --skip-hold
```

- **Windows** (from `rental_backend/core`):  
  `scripts\run_referral_e2e.bat`

- **Linux/Mac** (from `rental_backend/core`):  
  `chmod +x scripts/run_referral_e2e.sh && ./scripts/run_referral_e2e.sh`

## What the test does

1. **ReferralSettings** – Uses existing or creates default (reward ₹100, min order ₹300, hold 7 days).
2. **Referrer (User A)** – Creates a user with a referral code.
3. **Referred (User B)** – Creates a user with `referred_by=A` and a **Referral** record (pending).
4. **Order** – Creates an order for User B with amount ≥ minimum (if you have at least one ProductOption in DB). If no product exists, it only simulates referral completion.
5. **Delivery** – Marks the order’s `OrderedProduct` as **DELIVERED** so the referral is marked **completed** (and `hold_until` is set).
6. **Credit** – Simulates “Approve & credit”: adds reward to referrer’s wallet, creates a **WalletTransaction** (credit), and marks referral **rewarded**.
7. **Assertions** – Checks referral status, wallet balance, and that a credit transaction exists.
8. **Cleanup** – By default deletes the test users, referral, order, and wallet transaction. Use `--no-clean` to keep them.

## Options

| Option       | Description |
|-------------|-------------|
| `--skip-hold` | Credit reward immediately (set `hold_until` in the past). **Default: on** so the test does not wait. |
| `--no-clean`  | Do not delete test users and data at the end (useful to inspect in Admin). |

## Examples

```bash
# Default: skip hold, cleanup at end
python manage.py run_referral_e2e

# Keep test data to inspect in Django Admin
python manage.py run_referral_e2e --no-clean
```

## Expected output

You should see something like:

```
============================================================
REFERRAL E2E TEST
============================================================

[1/8] ReferralSettings...
  Using existing: Referral settings (reward ₹100, wallet 20%)

[2/8] Creating referrer (User A)...
  Referrer: e2e_referrer_...@test.local, code: E2EREF...

...

*** REFERRAL E2E TEST PASSED ***
```

If anything fails, the command prints which step failed and exits with a non-zero code.

## Requirements

- Django project with migrations applied (`python manage.py migrate`).
- If you get `UnicodeEncodeError` when running (e.g. on Windows console), set `PYTHONIOENCODING=utf-8` or run from a terminal that supports UTF-8.
- **ReferralSettings** can be created by the script if missing.
- For the **full** flow (real order → delivered → completion), the database should have at least one **ProductOption** (and thus Product + Category). If none exist, the script still runs and simulates referral completion so wallet credit and assertions are tested.
