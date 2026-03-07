"""
FCM (Firebase Cloud Messaging) utilities for sending push notifications.
Uses Firebase Admin SDK; ensure FIREBASE_ADMIN_CREDENTIALS is set in settings.
"""
from django.conf import settings

# Batch size for multicast (FCM limit is 500 per request)
FCM_MULTICAST_BATCH_SIZE = 500


def _get_tokens_for_users(users):
    """Return list of FCM tokens for the given user queryset or list of User instances."""
    from backend.models import UserDevice
    if hasattr(users, 'values_list'):
        user_ids = list(users.values_list('id', flat=True))
    else:
        user_ids = [u.id for u in users]
    return list(
        UserDevice.objects.filter(user_id__in=user_ids)
        .values_list('fcm_token', flat=True)
        .distinct()
    )


def _send_to_tokens(tokens, title, body, data=None):
    """
    Send FCM notification to a list of tokens.
    Returns (success_count, failure_count). Batches if len(tokens) > FCM_MULTICAST_BATCH_SIZE.
    """
    if not tokens:
        return 0, 0
    import firebase_admin
    from firebase_admin import messaging
    try:
        firebase_admin.get_app()
    except ValueError:
        print('FCM: Firebase Admin not initialized')
        return 0, len(tokens)
    data = data or {}
    # Ensure all data values are strings (FCM requirement)
    data = {k: str(v) for k, v in data.items()}
    android_config = messaging.AndroidConfig(priority='high')
    total_success, total_failure = 0, 0
    for i in range(0, len(tokens), FCM_MULTICAST_BATCH_SIZE):
        batch_tokens = tokens[i : i + FCM_MULTICAST_BATCH_SIZE]
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=data,
            android=android_config,
            tokens=batch_tokens,
        )
        batch = messaging.send_each_for_multicast(message)
        total_success += batch.success_count
        total_failure += batch.failure_count
    return total_success, total_failure


def send_fcm_to_user(user, title, body, data=None):
    """
    Send push notification to a single user (all their registered devices).
    Returns (success_count, failure_count).
    """
    tokens = _get_tokens_for_users([user])
    if not tokens:
        print(f'FCM: No tokens for user {user.id} ({getattr(user, "email", "")})')
        return 0, 0
    return _send_to_tokens(tokens, title, body, data)


def send_fcm_to_users(users, title, body, data=None):
    """
    Send push notification to multiple users.
    users: queryset of User or list of User.
    Returns (success_count, failure_count).
    """
    tokens = _get_tokens_for_users(users)
    if not tokens:
        return 0, 0
    return _send_to_tokens(tokens, title, body, data)


def send_fcm_to_all_users(title, body, data=None):
    """
    Send push notification to all users that have at least one FCM token (broadcast).
    Returns (success_count, failure_count).
    """
    from backend.models import UserDevice
    tokens = list(UserDevice.objects.values_list('fcm_token', flat=True).distinct())
    if not tokens:
        print('FCM: No device tokens in database')
        return 0, 0
    return _send_to_tokens(tokens, title, body, data)
