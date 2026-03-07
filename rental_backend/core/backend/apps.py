from django.apps import AppConfig


class BackendConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'backend'

    def ready(self):
        try:
            from django.conf import settings
            creds = getattr(settings, 'FIREBASE_ADMIN_CREDENTIALS', None)
            if creds is not None:
                import firebase_admin
                try:
                    firebase_admin.get_app()
                except ValueError:
                    firebase_admin.initialize_app(creds)
                    print('✅ Firebase Admin initialized for FCM push')
        except Exception as e:
            print(f'⚠️ Firebase Admin init (optional): {e}')
