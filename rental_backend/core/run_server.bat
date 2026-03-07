@echo off
REM Run Django so the app on phone/emulator can connect (0.0.0.0 = listen on all interfaces)
python manage.py runserver 0.0.0.0:8000
