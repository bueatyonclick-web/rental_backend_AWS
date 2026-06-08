# Rental Backend Core Folder Structure

This file documents the current structure of the `rental_backend/core` folder.

## Root of `rental_backend/core`

- backend/
  - admin.py
  - apps.py
  - authentication.py
  - beautyonclick-45c23-firebase-adminsdk-fbsvc-e78a918a62.json
  - fcm_utils.py
  - management/
  - migrations/
  - models.py
  - rental_backend_update4.code-workspace
  - serializers.py
  - templates/
    - admin/
  - tests.py
  - urls.py
  - utils.py
  - views.py
  - __init__.py
- core/
  - __init__.py
  - asgi.py
  - settings.py
  - urls.py
  - wsgi.py
- current_db_structure.txt
- db.sqlite3
- manage.py
- media/
- README.md
- requirements.txt
- run_for_phone.bat
- run_server.bat
- scripts/
- staticfiles/
- SUPERADMIN_COMMANDS.md

## Notes

- `core/` contains the Django project package with `settings.py`, `urls.py`, and `wsgi.py`.
- `backend/` contains the main Django app code and templates.
- `media/` and `staticfiles/` are present for uploaded files and collected static assets.
