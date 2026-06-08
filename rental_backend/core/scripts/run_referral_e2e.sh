#!/usr/bin/env bash
# Run Referral E2E test from rental_backend/core
# Usage: ./scripts/run_referral_e2e.sh   OR   cd rental_backend/core && ./scripts/run_referral_e2e.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(dirname "$SCRIPT_DIR")"
cd "$CORE_DIR" || exit 1

echo "Running Referral E2E test (Django management command)..."
python manage.py run_referral_e2e --skip-hold
EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "[OK] Test finished. Check output above for PASS/FAIL."
else
    echo "[!!] Command exited with code $EXIT_CODE"
fi
exit $EXIT_CODE
