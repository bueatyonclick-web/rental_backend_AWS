@echo off
REM Run Referral E2E test from repo root or from rental_backend/core
REM Usage: run_referral_e2e.bat   OR   cd rental_backend\core && run_referral_e2e.bat

set SCRIPT_DIR=%~dp0
set CORE_DIR=%SCRIPT_DIR%..
cd /d "%CORE_DIR%"

echo Running Referral E2E test (Django management command)...
python manage.py run_referral_e2e --skip-hold
set EXIT_CODE=%ERRORLEVEL%
echo.
if %EXIT_CODE% equ 0 (
    echo [OK] Test finished. Check output above for PASS/FAIL.
) else (
    echo [!!] Command exited with code %EXIT_CODE%
)
exit /b %EXIT_CODE%
