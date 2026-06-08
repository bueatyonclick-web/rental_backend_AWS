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

from backend.models import Otp, Token, PasswordResetToken, Notification, VendorToken, ServiceVendorToken
from backend.serializers import NotificationSerializer
from core.settings import (
    HYPERsender_API_KEY,
    HYPERsender_INSTANCE_ID,
    HYPERsender_WHATSAPP_BASE_URL,
)
from django.db.models import Q
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


class IsAuthenticatedServiceVendor(BasePermission):
    message = 'unauthenticated_service_vendor'

    def has_permission(self, request, view):
        from backend.models import ServiceVendor
        return isinstance(request.user, ServiceVendor) and request.user.is_active


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


def validate_coupon_and_calculate_discount(
    coupon_code,
    user,
    cart_total,
    product_option_ids=None,
    product_ids=None,
    service_ids=None,
):
    """
    Validate a coupon for the given user and cart, and calculate discount.
    Returns: (success: bool, message: str, discount_amount: int, final_total: int, coupon_obj or None)

    - product_option_ids: list of ProductOption UUIDs in cart (for product eligibility)
    - product_ids: list of Product UUIDs in cart (alternative; we derive from product_option if needed)
    - service_ids: list of Service UUIDs in cart (for service eligibility)
    """
    from decimal import Decimal
    from backend.models import Coupon, Order, ProductOption

    product_option_ids = product_option_ids or []
    product_ids = list(product_ids) if product_ids else []
    service_ids = service_ids or []

    # Resolve product IDs from product_option_ids if needed
    if product_option_ids and not product_ids:
        product_ids = list(
            ProductOption.objects.filter(id__in=product_option_ids)
            .values_list('product_id', flat=True)
            .distinct()
        )

    coupon_code = (coupon_code or '').strip().upper()
    if not coupon_code:
        return False, 'Coupon code is required', 0, int(cart_total), None

    try:
        coupon = Coupon.objects.prefetch_related(
            'applicable_products', 'applicable_categories', 'applicable_services'
        ).get(code=coupon_code)
    except Coupon.DoesNotExist:
        return False, 'Invalid or expired coupon code', 0, int(cart_total), None

    if not coupon.is_active:
        return False, 'Invalid or expired coupon code', 0, int(cart_total), None

    now = timezone.now()
    if now < coupon.valid_from:
        return False, 'This coupon is not yet valid', 0, int(cart_total), None
    if now > coupon.valid_until:
        return False, 'Invalid or expired coupon code', 0, int(cart_total), None

    if coupon.usage_limit > 0 and coupon.used_count >= coupon.usage_limit:
        return False, 'This coupon has reached its usage limit', 0, int(cart_total), None

    if coupon.first_order_only:
        if Order.objects.filter(user=user).exists():
            return False, 'This coupon is valid for first order only', 0, int(cart_total), None

    cart_total_decimal = Decimal(str(cart_total))
    if cart_total_decimal < coupon.minimum_order_amount:
        return (
            False,
            f'Minimum order value of ₹{coupon.minimum_order_amount} required for this coupon',
            0,
            int(cart_total),
            None,
        )

    # Service booking: only check applicable_services (ignore product/category for this request)
    if service_ids:
        if coupon.applicable_services.exists():
            # Normalize to strings for comparison (DB returns UUIDs, request sends strings)
            allowed_service_ids = {str(sid).strip().lower() for sid in coupon.applicable_services.values_list('id', flat=True)}
            request_service_ids = {str(sid).strip().lower() for sid in service_ids}
            if not (request_service_ids & allowed_service_ids):
                return False, 'This coupon is not applicable to this service', 0, int(cart_total), None
        # Service request passed (or coupon has no service restriction). Skip product/category checks.
    else:
        # Product/cart order: check applicable_products and applicable_categories
        if coupon.applicable_products.exists():
            if not product_ids:
                return False, 'This coupon is not applicable to your cart items', 0, int(cart_total), None
            allowed_product_ids = set(coupon.applicable_products.values_list('id', flat=True))
            if not (set(product_ids) & allowed_product_ids):
                return False, 'This coupon is not applicable to your cart items', 0, int(cart_total), None

        if coupon.applicable_categories.exists():
            if not product_ids:
                return False, 'This coupon is not applicable to your cart items', 0, int(cart_total), None
            from backend.models import Product
            cart_category_ids = set(
                Product.objects.filter(id__in=product_ids).values_list('category_id', flat=True)
            )
            allowed_cat_ids = set(coupon.applicable_categories.values_list('id', flat=True))
            if not (cart_category_ids & allowed_cat_ids):
                return False, 'This coupon is not applicable to your cart items', 0, int(cart_total), None

    # Calculate discount
    if coupon.discount_type == Coupon.DISCOUNT_PERCENTAGE:
        discount = (cart_total_decimal * coupon.discount_value) / Decimal('100')
    else:
        discount = coupon.discount_value

    if coupon.maximum_discount_amount is not None and coupon.maximum_discount_amount > 0:
        discount = min(discount, coupon.maximum_discount_amount)

    discount = int(discount)
    discount = min(discount, int(cart_total))
    final_total = max(0, int(cart_total) - discount)

    return True, 'Coupon applied successfully', discount, final_total, coupon


def get_available_categories_for_pincode(pincode):
    """
    If this serviceable location has any CategoryAvailability rows, only categories
    explicitly enabled there are shown (whitelist for that pincode).

    If the location has no rows yet (not configured in admin), use legacy rules:
    categories with no availability rows anywhere are global; categories with rows
    only appear in pincodes where they are enabled.
    """
    from backend.models import ServiceableLocation, CategoryAvailability, Category

    try:
        location = ServiceableLocation.objects.get(pincode=pincode, is_active=True)
    except ServiceableLocation.DoesNotExist:
        return Category.objects.none()

    if CategoryAvailability.objects.filter(location=location).exists():
        available_ids = CategoryAvailability.objects.filter(
            location=location,
            is_available=True,
        ).values_list('category_id', flat=True)
        return Category.objects.filter(id__in=available_ids).order_by('position')

    restricted_ids = CategoryAvailability.objects.values_list(
        'category_id', flat=True
    ).distinct()
    if not restricted_ids:
        return Category.objects.all().order_by('position')

    unrestricted_qs = Category.objects.exclude(id__in=restricted_ids)
    restricted_here_qs = Category.objects.filter(
        location_availability__location=location,
        location_availability__is_available=True,
    )
    return (
        Category.objects.filter(
            Q(id__in=unrestricted_qs.values('id'))
            | Q(id__in=restricted_here_qs.values('id'))
        )
        .distinct()
        .order_by('position')
    )


def get_available_page_items_for_pincode(pincode):
    """
    Same whitelist vs legacy split as get_available_categories_for_pincode.
    """
    from backend.models import ServiceableLocation, PageItemAvailability, PageItem

    try:
        location = ServiceableLocation.objects.get(pincode=pincode, is_active=True)
    except ServiceableLocation.DoesNotExist:
        return PageItem.objects.none()

    if PageItemAvailability.objects.filter(location=location).exists():
        available_ids = PageItemAvailability.objects.filter(
            location=location,
            is_available=True,
        ).values_list('page_item_id', flat=True)
        return PageItem.objects.filter(id__in=available_ids).order_by('position')

    restricted_ids = PageItemAvailability.objects.values_list(
        'page_item_id', flat=True
    ).distinct()
    if not restricted_ids:
        return PageItem.objects.all().order_by('position')

    unrestricted_qs = PageItem.objects.exclude(id__in=restricted_ids)
    restricted_here_qs = PageItem.objects.filter(
        location_availability__location=location,
        location_availability__is_available=True,
    )
    return (
        PageItem.objects.filter(
            Q(id__in=unrestricted_qs.values('id'))
            | Q(id__in=restricted_here_qs.values('id'))
        )
        .distinct()
        .order_by('position')
    )


def get_available_service_categories_for_pincode(pincode):
    """
    Same whitelist vs legacy split as get_available_categories_for_pincode.
    """
    from backend.models import ServiceableLocation, ServiceCategoryAvailability, ServiceCategory

    try:
        location = ServiceableLocation.objects.get(pincode=pincode, is_active=True)
    except ServiceableLocation.DoesNotExist:
        return ServiceCategory.objects.none()

    if ServiceCategoryAvailability.objects.filter(location=location).exists():
        available_ids = ServiceCategoryAvailability.objects.filter(
            location=location,
            is_available=True,
        ).values_list('service_category_id', flat=True)
        return ServiceCategory.objects.filter(id__in=available_ids).order_by('position')

    restricted_ids = ServiceCategoryAvailability.objects.values_list(
        'service_category_id', flat=True
    ).distinct()
    if not restricted_ids:
        return ServiceCategory.objects.all().order_by('position')

    unrestricted_qs = ServiceCategory.objects.exclude(id__in=restricted_ids)
    restricted_here_qs = ServiceCategory.objects.filter(
        location_availability__location=location,
        location_availability__is_available=True,
    )
    return (
        ServiceCategory.objects.filter(
            Q(id__in=unrestricted_qs.values('id'))
            | Q(id__in=restricted_here_qs.values('id'))
        )
        .distinct()
        .order_by('position')
    )


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