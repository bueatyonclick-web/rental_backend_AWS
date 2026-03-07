@echo off
REM Run Django so the app on your phone can connect (same Wi-Fi).
REM Listens on 0.0.0.0:8000 so 10.154.252.150:8000 works from the phone.
cd /d "%~dp0"
echo Starting backend for phone access at http://10.154.252.150:8000
echo.
python manage.py runserver 0.0.0.0:8000
pause
