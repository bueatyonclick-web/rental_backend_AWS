# Add Superadmin (Django Admin)

Use Django's built-in command to create a superuser that can log into the admin panel at `/admin/`.

## Command (run from project core folder)

```bash
cd C:\rental_backend\rental_backend\core
python manage.py createsuperuser
```

You will be prompted for:
- **Username** (e.g. `admin` or `superadmin`)
- **Email** (your email)
- **Password** (twice; it won’t show while typing)

## One-liner (non-interactive, for scripts)

```bash
cd C:\rental_backend\rental_backend\core
python manage.py createsuperuser --noinput --username superadmin --email admin@example.com
```

Then set the password in Django shell:

```bash
python manage.py shell -c "from django.contrib.auth import get_user_model; u = get_user_model().objects.get(username='superadmin'); u.set_password('YourSecurePassword'); u.save(); print('Password set.')"
```

Replace `superadmin`, `admin@example.com`, and `YourSecurePassword` with your values.

## After creating

1. Start the server: `python manage.py runserver`
2. Open: http://127.0.0.1:8000/admin/
3. Log in with the username and password you set.
