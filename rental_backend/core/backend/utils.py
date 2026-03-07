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
from core.settings import (
    HYPERsender_API_KEY,
    HYPERsender_INSTANCE_ID,
    HYPERsender_WHATSAPP_BASE_URL,
)
from django.utils import timezone

from twilio.rest import Client
import logging

logger = logging.getLogger(__name__)


def _send_otp_via_hypersender_whatsapp(phone_str, otp):
    """
    Send OTP via HyperSender WhatsApp. Tries send-text-safe then send-text.
    chat_id format: country code + number, no + (e.g. 919876543210@c.us for India).
    """
    chat_id = f"91{phone_str}@c.us"
    text = f"Your OTP for sign up is *{otp}*. Valid for 10 minutes. Do not share with anyone."
    payload = {"chatId": chat_id, "text": text}
    headers = {
        "Authorization": f"Bearer {HYPERsender_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    for endpoint in ("send-text-safe", "send-text"):
        url = f"{HYPERsender_WHATSAPP_BASE_URL}/{HYPERsender_INSTANCE_ID}/{endpoint}"
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=20)
            data = {}
            if resp.text:
                try:
                    data = resp.json()
                except Exception:
                    data = {"_raw": resp.text[:500]}

            logger.info("HyperSender %s: status=%s, body=%s", endpoint, resp.status_code, data)
            print(f"[OTP WhatsApp] {endpoint} -> status={resp.status_code}, response={data}")

            if resp.status_code in (200, 201, 202):
                if data.get("queued") is True or "queued_request_uuid" in data:
                    logger.info("OTP queued for WhatsApp to %s", chat_id)
                    return True
                logger.info("OTP accepted for WhatsApp %s (2xx)", chat_id)
                return True

            if resp.status_code == 401:
                logger.error("HyperSender 401: Check API key (HYPERsender_API_KEY)")
                print("[OTP WhatsApp] 401 - Check your HyperSender API key")
            elif resp.status_code == 403:
                logger.error("HyperSender 403: Check instance ID and API key")
                print("[OTP WhatsApp] 403 - Check instance ID and API key")
            elif resp.status_code == 422:
                err = data.get("errors") or data.get("message") or data
                logger.error("HyperSender 422: %s (chatId or number not on WhatsApp?)", err)
                print(f"[OTP WhatsApp] 422 - Check chatId {chat_id} or if number is on WhatsApp: {err}")
        except requests.exceptions.RequestException as e:
            logger.exception("HyperSender request failed (%s): %s", endpoint, e)
            print(f"[OTP WhatsApp] Request error: {e}")
        except Exception as e:
            logger.exception("HyperSender error (%s): %s", endpoint, e)
            print(f"[OTP WhatsApp] Error: {e}")

    return False


def send_otp(phone):
    """
    Send OTP only via HyperSender WhatsApp. No SMS and no phone call.
    """
    try:
        phone_str = str(phone).strip()
        if phone_str.startswith("+91"):
            phone_str = phone_str[3:]
        elif phone_str.startswith("91") and len(phone_str) == 12:
            phone_str = phone_str[2:]
        phone_str = "".join(filter(str.isdigit, phone_str))

        if len(phone_str) != 10:
            return Response({
                "success": False,
                "message": "Invalid phone number format (must be 10 digits)",
            }, status=400)

        otp = randint(100000, 999999)
        validity = timezone.now() + datetime.timedelta(minutes=10)
        Otp.objects.update_or_create(
            phone=phone_str,
            defaults={"otp": otp, "verified": False, "validity": validity},
        )

        if not _send_otp_via_hypersender_whatsapp(phone_str, otp):
            return Response({
                "success": False,
                "message": "Could not send OTP to WhatsApp. Please ensure this number is on WhatsApp and try again.",
            }, status=400)

        return Response({
            "success": True,
            "message": "OTP sent successfully to your WhatsApp",
            "phone": phone_str,
        }, status=200)

    except requests.exceptions.RequestException as e:
        logger.error(f"OTP request error: {str(e)}")
        return Response({
            "success": False,
            "message": "Service temporarily unavailable. Please try again.",
        }, status=500)
    except Exception as e:
        logger.error(f"Unexpected error in send_otp: {str(e)}")
        return Response({
            "success": False,
            "message": "Unexpected error occurred",
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


def get_pincode_from_coordinates(latitude, longitude):
    """
    Get pincode from latitude/longitude using geocoding
    You can use geopy library for this
    """
    try:
        from geopy.geocoders import Nominatim

        geolocator = Nominatim(user_agent="rental_app")
        location = geolocator.reverse(f"{latitude}, {longitude}", language='en')

        if location and location.raw.get('address'):
            address = location.raw['address']
            pincode = address.get('postcode', '')

            # Clean pincode (remove spaces, take first 6 digits)
            pincode = ''.join(filter(str.isdigit, pincode))[:6]

            return pincode if len(pincode) == 6 else None

        return None
    except Exception as e:
        print(f"Error getting pincode from coordinates: {e}")
        return None


def check_pincode_serviceability(pincode):
    """
    Check if a pincode is serviceable
    Returns: (is_serviceable, location_obj or None, message)
    """
    from backend.models import ServiceableLocation

    try:
        location = ServiceableLocation.objects.get(
            pincode=pincode,
            is_active=True
        )
        return True, location, f"Service available in {location.area_name}"
    except ServiceableLocation.DoesNotExist:
        return False, None, "We're coming soon to your location! ðŸš€"


def get_available_categories_for_pincode(pincode):
    """
    Get categories available for a specific pincode
    """
    from backend.models import ServiceableLocation, CategoryAvailability, Category

    try:
        location = ServiceableLocation.objects.get(pincode=pincode, is_active=True)

        # Get categories with explicit availability
        available_cat_ids = CategoryAvailability.objects.filter(
            location=location,
            is_available=True
        ).values_list('category_id', flat=True)

        if available_cat_ids:
            return Category.objects.filter(id__in=available_cat_ids).order_by('position')
        else:
            # If no specific availability set, return all categories
            return Category.objects.all().order_by('position')

    except ServiceableLocation.DoesNotExist:
        return Category.objects.none()


def get_available_page_items_for_pincode(pincode):
    """
    Get page items available for a specific pincode
    """
    from backend.models import ServiceableLocation, PageItemAvailability, PageItem

    try:
        location = ServiceableLocation.objects.get(pincode=pincode, is_active=True)

        # Get page items with explicit availability
        available_item_ids = PageItemAvailability.objects.filter(
            location=location,
            is_available=True
        ).values_list('page_item_id', flat=True)

        if available_item_ids:
            return PageItem.objects.filter(id__in=available_item_ids).order_by('position')
        else:
            # If no specific availability set, return all page items
            return PageItem.objects.all().order_by('position')

    except ServiceableLocation.DoesNotExist:
        return PageItem.objects.none()


def get_available_service_categories_for_pincode(pincode):
    """
    Get service categories available for a specific pincode
    """
    from backend.models import ServiceableLocation, ServiceCategoryAvailability, ServiceCategory

    try:
        location = ServiceableLocation.objects.get(pincode=pincode, is_active=True)

        # Get service categories with explicit availability
        available_cat_ids = ServiceCategoryAvailability.objects.filter(
            location=location,
            is_available=True
        ).values_list('service_category_id', flat=True)

        if available_cat_ids:
            return ServiceCategory.objects.filter(id__in=available_cat_ids).order_by('position')
        else:
            # If no specific availability set, return all service categories
            return ServiceCategory.objects.all().order_by('position')

    except ServiceableLocation.DoesNotExist:
        return ServiceCategory.objects.none()


def get_coordinates_from_location(pincode, area_name, city=None, state=None):
    """
    Get latitude and longitude from pincode and area name using geocoding
    Returns: (latitude, longitude) or (None, None) if not found
    """
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderTimedOut, GeocoderServiceError

        geolocator = Nominatim(user_agent="rental_app")

        # Build search query with available information
        search_parts = []
        if area_name:
            search_parts.append(area_name)
        if city:
            search_parts.append(city)
        if state:
            search_parts.append(state)
        if pincode:
            search_parts.append(pincode)
        search_parts.append("India")

        search_query = ", ".join(search_parts)

        print(f"Searching coordinates for: {search_query}")

        # Try geocoding with full query
        location = geolocator.geocode(search_query, timeout=10)

        if location:
            print(f"Found coordinates: {location.latitude}, {location.longitude}")
            return round(location.latitude, 6), round(location.longitude, 6)

        # If full query fails, try with just pincode
        if pincode:
            print(f" Trying with pincode only: {pincode}")
            location = geolocator.geocode(f"{pincode}, India", timeout=10)
            if location:
                print(f"Found coordinates: {location.latitude}, {location.longitude}")
                return round(location.latitude, 6), round(location.longitude, 6)

        print(" Could not find coordinates")
        return None, None

    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print(f"Geocoding service error: {e}")
        return None, None
    except Exception as e:
        print(f" Error getting coordinates: {e}")
        return None, None