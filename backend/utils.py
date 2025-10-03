import base64
import datetime
import hashlib
import hmac
import uuid
from random import randint

import requests
from django.core.mail import EmailMessage
from django.template.loader import get_template
from google.auth import jwt
from google.auth.transport.requests import Request
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from backend.models import Otp, Token, PasswordResetToken, Notification
from backend.serializers import NotificationSerializer
from core.settings import TEMPLATES_BASE_URL, CF_KEY, FSMS_KEY, SERVICE_ACCOUNT_FILE, PROJECT_ID

from django.utils import timezone


def get_access_token():
    """
    Generate an access token using a service account.
    """
    credentials = jwt.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE,
                                                            audience='https://fcm.googleapis.com/')
    credentials.refresh(Request())
    return credentials.token.decode('utf-8') if isinstance(credentials.token, bytes) else credentials.token


def send_user_notification(user, title, body, image):
    # Save the notification to the database
    notif = Notification(user=user, title=title, body=body, image=image)
    notif.save()

    notif_data = NotificationSerializer(notif, many=False).data
    message_title = notif_data.get('title')
    message_body = notif_data.get('body')
    message_image = notif_data.get('image')

    # Retrieve the most recent FCM token for the user
    latest_token = user.tokens_set.order_by('-created_at').first()

    if not latest_token:
        return Response({'message': 'No FCM token available'}, status=400)

    message = {
        "message": {
            "notification": {
                "title": message_title,
                "body": message_body,
                "image": message_image,
            },
            "data": {
                "image": message_image,
            },
            "android": {
                "notification": {
                    "sound": "default",
                }
            },
            "token": latest_token.fcmtoken
        }
    }

    # Set up the FCM endpoint and headers
    url = f'https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send'
    headers = {
        'Authorization': 'Bearer ' + get_access_token(),
        'Content-Type': 'application/json; UTF-8',
    }

    # Send the POST request to FCM
    response = requests.post(url, json=message, headers=headers)

    # Handle the response
    if response.status_code == 200:
        print('Successfully sent message')
        return Response({'message': 'Notification sent successfully'})
    else:
        error = response.json()
        print('Error sending message:', error)
        return Response({'message': 'Failed to send notification', 'error': error}, status=400)


def send_otp(phone):
    otp = randint(100000, 999999)
    validity = timezone.now() + datetime.timedelta(minutes=10)
    Otp.objects.update_or_create(phone=phone, defaults={"otp": otp, "verified": False, "validity": validity})

    print(otp)
    return Response("Otp sent Successfully")


def new_token():
    token = uuid.uuid1().hex
    return token


def token_response(user, fcmtoken):
    token = new_token()
    Token.objects.create(token=token, user=user, fcmtoken=fcmtoken)
    return Response('token ' + token)


def send_password_reset_email(user):
    token = new_token()
    exp_time = datetime.datetime.now() + datetime.timedelta(minutes=10)

    PasswordResetToken.objects.update_or_create(user=user,
                                                defaults={'user': user, 'token': token, 'validity': exp_time})

    email_data = {
        'token': token,
        'email': user.email,
        'base_url': TEMPLATES_BASE_URL
    }

    message = get_template('emails/reset-password.html').render(email_data)

    msg = EmailMessage('Reset Password', body=message, to=[user.email])
    msg.content_subtype = 'html'

    try:
        msg.send()
    except Exception as e:
        print(f'Error sending email: {e}')
        return Response('email_failed', status=500)

    return Response('reset_password_email_sent')


class IsAuthenticatedUser(BasePermission):
    message = 'unauthenticated_user'

    def has_permission(self, request, view):
        return bool(request.user)


def cfSignature(postData):
    signatureData = postData["orderId"] + postData["orderAmount"] + postData["referenceId"] + postData["txStatus"] + \
                    postData["paymentMode"] + postData["txMsg"] + postData["txTime"]

    message = bytes(signatureData, encoding='utf8')
    secret = bytes(CF_KEY, encoding='utf8')
    signature = base64.b64encode(hmac.new(secret, message, digestmod=hashlib.sha256).digest())

    return signature