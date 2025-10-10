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

from backend.models import Otp, Token, PasswordResetToken, Notification, VendorToken
from backend.serializers import NotificationSerializer
from core.settings import TWO_FACTOR_API_KEY
from django.utils import timezone

from twilio.rest import Client
import logging

logger = logging.getLogger(__name__)



def send_otp(phone):
    """
    Send OTP using 2Factor.in via SMS (NO CALL).
    """
    try:
        # Clean and validate phone number
        phone_str = str(phone).strip().replace("+91", "").replace("91", "", 1)
        if len(phone_str) != 10 or not phone_str.isdigit():
            return Response({
                "success": False,
                "message": "Invalid phone number format (must be 10 digits)"
            }, status=400)

        # Generate OTP manually
        otp = randint(100000, 999999)
        validity = timezone.now() + datetime.timedelta(minutes=10)

        # Save OTP in database
        Otp.objects.update_or_create(
            phone=phone_str,
            defaults={"otp": otp, "verified": False, "validity": validity}
        )

        # ✅ 2Factor SMS-Only OTP API endpoint
        url = f"https://2factor.in/API/V1/{TWO_FACTOR_API_KEY}/SMS/{phone_str}/{otp}/SMS"

        logger.info(f"Sending OTP to {phone_str} via 2Factor SMS API")

        # Make GET request to send SMS
        response = requests.get(url, timeout=10)
        response_data = response.json()

        logger.info(f"2Factor Response: {response_data}")

        # Check for success
        if response_data.get("Status") == "Success":
            print(f"✅ OTP sent via SMS to {phone_str}: {otp}")
            return Response({
                "success": True,
                "message": "OTP sent successfully via SMS",
                "phone": phone_str
            }, status=200)

        # Handle error from 2Factor
        msg = response_data.get("Details", "Failed to send SMS OTP")
        logger.error(f"2Factor Error: {msg}")
        return Response({
            "success": False,
            "message": f"SMS failed: {msg}"
        }, status=400)

    except requests.exceptions.RequestException as e:
        logger.error(f"2Factor request error: {str(e)}")
        return Response({
            "success": False,
            "message": "SMS service temporarily unavailable"
        }, status=500)

    except Exception as e:
        logger.error(f"Unexpected error in send_otp: {str(e)}")
        return Response({
            "success": False,
            "message": "Unexpected error occurred"
        }, status=500)


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



def vendor_token_response(vendor, fcmtoken):
    """Generate token response for vendors"""
    token = new_token()
    VendorToken.objects.create(token=token, vendor=vendor, fcmtoken=fcmtoken)
    return Response('token ' + token)


class IsAuthenticatedUser(BasePermission):
    message = 'unauthenticated_user'

    def has_permission(self, request, view):
        return bool(request.user)


class IsAuthenticatedVendor(BasePermission):
    """Permission class for vendors"""
    message = 'unauthenticated_vendor'

    def has_permission(self, request, view):
        # Check if request.user is actually a Vendor instance
        from backend.models import Vendor
        return isinstance(request.user, Vendor) and request.user.is_active

