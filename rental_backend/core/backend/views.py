import base64
import uuid
from datetime import datetime, date, time, timedelta
import hashlib
import hmac
import json
import logging
import math
from calendar import monthrange, calendar
from random import randint

from django.utils import timezone
import datetime

import razorpay
import requests
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.db import transaction
from django.contrib.auth.hashers import make_password, check_password
from django.db.models import Q, Case, When, IntegerField, Value, F, Count, Avg, Sum, Exists, OuterRef
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import get_template
from google.auth import jwt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes, authentication_classes
from rest_framework.generics import get_object_or_404
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.views import APIView

from django.utils.html import strip_tags
from django.utils.encoding import force_str

from backend.models import User, Otp, Token, Category, Slide, HomeBanner, Product, PageItem, ProductOption, Order, \
    OrderedProduct, Service, ServiceBooking, ServicePageItem, ServiceCategory, ServiceSubCategory, ServiceOption, PasswordResetToken, \
    ProductImage, Vendor, VendorToken, ProductBooking, CartItem, Notification, VendorProduct, UserAddress, \
    ServiceableLocation, HomePageItem, ServiceWishlistItem, UserDevice, ArtistAvailability, CategoryAvailability, \
    ReferralSettings, Referral, WalletTransaction, ServiceVendor, ServiceVendorToken
from backend.serializers import UserSerializer, CategorySerializer, SlideSerializer, PageItemSerializer, \
    ProductSerializer, WishlistSerializer, CartSerializer, AddressSerializer, ItemOrderSerializer, \
    OrderDetailsSerializer, NotificationSerializer, OrderItemSerializer, ProductOptionSerializer, InformMeSerializer, \
    VersionCheckRequestSerializer, ServiceSerializer, ServiceCategorySerializer, ServiceSubCategorySerializer, ServicePageItemSerializer, \
    ServiceBookingSerializer, ServiceBookingDetailSerializer, VendorOrderDetailSerializer, HomePageItemSerializer, \
    ServiceWishlistItemSerializer

from backend.utils import send_otp, token_response, send_password_reset_email, IsAuthenticatedUser, \
    new_token, IsAuthenticatedVendor, IsAuthenticatedServiceVendor
from core import settings
from core.settings import TEMPLATES_BASE_URL
from rest_framework import status as http_status

from . import models
from .authentication import VendorTokenAuthentication, ServiceVendorTokenAuthentication
from .serializers import PrivacyPolicySerializer

from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def _notify_user_trial_decision(user, title, body, data=None):
    """
    Send push + create in-app Notification row for trial decision.
    Best-effort: never break vendor accept/reject if push fails.
    """
    try:
        # In-app notifications list
        Notification.objects.create(user=user, title=title, body=body)
    except Exception:
        pass

    try:
        from backend.fcm_utils import send_fcm_to_user
        send_fcm_to_user(
            user,
            title,
            body,
            data=data or {'screen': 'trial', 'type': 'trial_vendor_decision'},
        )
    except Exception as e:
        logger.warning('Trial decision push failed: %s', e)


# =============================================================================
# ANALYTICS: SCREEN VIEW EVENTS (Customer app)
# =============================================================================

@api_view(['POST'])
@permission_classes([AllowAny])
def analytics_screen_start(request):
    """
    Create a ScreenViewEvent row. Call when a screen becomes visible.
    Body:
      - screen: string (required)
      - device_id: string (optional)
      - session_id: string (optional)
      - platform: android/ios/web (optional)
      - app_version: string (optional)
    Auth:
      - If user token present, links to user.
      - If guest, user=null but device_id can be set for uniqueness.
    Returns:
      - event_id
    """
    from backend.models import ScreenViewEvent
    screen = (request.data.get('screen') or '').strip()
    if not screen:
        return Response({'success': False, 'message': 'screen is required'}, status=400)

    device_id = (request.data.get('device_id') or '').strip()
    session_id = (request.data.get('session_id') or '').strip()
    platform = (request.data.get('platform') or '').strip()
    app_version = (request.data.get('app_version') or '').strip()

    user = None
    try:
        # If request has authenticated user, attach it
        if getattr(request, 'user', None) and getattr(request.user, 'is_authenticated', False):
            user = request.user
    except Exception:
        user = None

    ev = ScreenViewEvent.objects.create(
        user=user if getattr(user, 'id', None) else None,
        device_id=device_id,
        session_id=session_id,
        screen=screen[:150],
        platform=platform[:20],
        app_version=app_version[:40],
    )
    return Response({'success': True, 'event_id': str(ev.id)})


@api_view(['POST'])
@permission_classes([AllowAny])
def analytics_screen_end(request):
    """
    Close a ScreenViewEvent row and store duration.
    Body:
      - event_id: uuid (required)
      - duration_seconds: int (optional; if not provided, computed via now-started_at)
    """
    from backend.models import ScreenViewEvent
    event_id = (request.data.get('event_id') or '').strip()
    if not event_id:
        return Response({'success': False, 'message': 'event_id is required'}, status=400)

    try:
        ev = ScreenViewEvent.objects.get(id=event_id)
    except ScreenViewEvent.DoesNotExist:
        return Response({'success': False, 'message': 'event not found'}, status=404)

    if ev.ended_at:
        return Response({'success': True, 'message': 'already ended', 'duration_seconds': ev.duration_seconds})

    dur = request.data.get('duration_seconds', None)
    try:
        if dur is not None:
            dur_int = int(dur)
            if dur_int < 0:
                dur_int = 0
        else:
            dur_int = int((timezone.now() - ev.started_at).total_seconds())
            if dur_int < 0:
                dur_int = 0
    except (ValueError, TypeError):
        dur_int = int((timezone.now() - ev.started_at).total_seconds())
        if dur_int < 0:
            dur_int = 0

    ev.ended_at = timezone.now()
    ev.duration_seconds = dur_int
    ev.save(update_fields=['ended_at', 'duration_seconds'])

    return Response({'success': True, 'duration_seconds': ev.duration_seconds})


@api_view(['POST'])
@permission_classes([AllowAny])
def analytics_location_ping(request):
    """
    Store customer last known location (logged-in or guest by device_id).
    Body:
      - device_id: string (required)
      - latitude: number (required)
      - longitude: number (required)
      - accuracy_m: number (optional)
      - platform: string (optional)
      - app_version: string (optional)
    """
    from backend.models import CustomerLocationPing

    device_id = (request.data.get('device_id') or '').strip()
    if not device_id:
        return Response({'success': False, 'message': 'device_id is required'}, status=400)

    lat = request.data.get('latitude')
    lng = request.data.get('longitude')
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return Response({'success': False, 'message': 'latitude/longitude must be numbers'}, status=400)

    acc = request.data.get('accuracy_m', 0) or 0
    try:
        acc = float(acc)
    except (TypeError, ValueError):
        acc = 0.0

    platform = (request.data.get('platform') or '').strip()
    app_version = (request.data.get('app_version') or '').strip()

    user = None
    try:
        if getattr(request, 'user', None) and getattr(request.user, 'is_authenticated', False):
            user = request.user
    except Exception:
        user = None

    CustomerLocationPing.objects.create(
        user=user if getattr(user, 'id', None) else None,
        device_id=device_id,
        latitude=lat,
        longitude=lng,
        accuracy_m=acc,
        platform=platform[:20],
        app_version=app_version[:40],
    )

    return Response({'success': True})


# Authentication APIs (existing code kept intact)
# views.py - Updated request_otp function
@api_view(['POST'])
def request_otp(request):
    email = request.data.get('email')
    phone = request.data.get('phone')

    if not email or not phone:
        return Response({
            'success': False,
            'message': 'Email and phone number are required'
        }, status=400)

    # ✅ Clean and validate phone number
    import re

    # Convert to string and remove any non-digit characters
    phone = re.sub(r'[^\d]', '', str(phone))

    # Validate phone number (exactly 10 digits)
    if not re.match(r'^\d{10}$', phone):
        return Response({
            'success': False,
            'message': 'Invalid phone number format (must be 10 digits)'
        }, status=400)

    # Validate email format
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_regex, email):
        return Response({
            'success': False,
            'message': 'Invalid email format'
        }, status=400)

    # Check if user already exists
    if User.objects.filter(email=email).exists():
        return Response({
            'success': False,
            'message': 'Email already registered'
        }, status=400)

    if User.objects.filter(phone=phone).exists():
        return Response({
            'success': False,
            'message': 'Phone number already registered'
        }, status=400)

    # Send OTP with cleaned phone number
    return send_otp(phone)

@api_view(['POST'])
def resend_otp(request):
    phone = request.data.get('phone')
    if not phone:
        return Response('data_missing', 400)
    return send_otp(phone)


@api_view(['POST'])
def verify_otp(request):
    phone = request.data.get('phone')
    otp = request.data.get('otp')

    otp_obj = get_object_or_404(Otp, phone=phone, verified=False)

    if otp_obj.validity.replace(tzinfo=None) > datetime.datetime.utcnow():
        if otp_obj.otp == int(otp):
            otp_obj.verified = True
            otp_obj.save()
            return Response('otp_verified_successfully')
        else:
            return Response('Incorrect otp', 400)
    else:
        return Response('otp expired', 400)


@api_view(['POST'])
def create_account(request):
    """
    Create a new user account after OTP verification.
    Extended with:
    - Phone/email uniqueness validation
    - Referral code support
    - Device/IP tracking for fraud detection
    - Automatic referral code generation
    """
    email = request.data.get('email')
    phone = request.data.get('phone')
    password = request.data.get('password')
    fullname = request.data.get('fullname')
    fcmtoken = request.data.get('fcmtoken')
    # App sends referral_code_input; accept both for compatibility
    referral_code_input = (
        request.data.get('referral_code_input') or request.data.get('referral_code') or ''
    ).strip().upper()
    device_id = (request.data.get('device_id') or '').strip()

    if not all([email, phone, password, fullname]):
        return Response('data_missing', 400)

    # Uniqueness checks (mirrors signup repo error messages)
    if User.objects.filter(email=email).exists():
        return Response('email already exists', 400)
    if User.objects.filter(phone=phone).exists():
        return Response('phone already exists', 400)

    # Ensure OTP was verified
    otp_obj = get_object_or_404(Otp, phone=phone, verified=True)

    # Determine signup IP
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        signup_ip = xff.split(',')[0].strip()
    else:
        signup_ip = request.META.get('REMOTE_ADDR')

    referred_by = None
    referral_record = None

    # Load referral settings (with safe defaults)
    from backend.models import ReferralSettings, Referral
    settings_obj = ReferralSettings.get_active()
    reward_amount = settings_obj.referral_reward_amount if settings_obj else 100
    max_referrals_per_day = settings_obj.max_referrals_per_day if settings_obj else 5

    # Resolve referrer if referral code provided
    log = logging.getLogger(__name__)
    referred_by = None
    if referral_code_input:
        log.info('[Referral] create_account: referral_code_input=%s, new_user_phone=%s, new_user_email=%s',
                 referral_code_input, phone, email)
        referred_by = User.objects.filter(referral_code=referral_code_input).first()
        if not referred_by:
            log.warning('[Referral] create_account: invalid_referral_code=%s (no user found)', referral_code_input)
            return Response('invalid_referral_code', 400)

        # Prevent self-referral by phone/email (extra safety)
        if referred_by.phone == phone or referred_by.email == email:
            log.warning('[Referral] create_account: self_referral blocked, referrer=%s', referred_by.email)
            return Response('self_referral_not_allowed', 400)

    # Create user first so we can assign generated referral_code
    user = User(
        email=email,
        phone=phone,
        fullname=fullname,
        password=make_password(password),
        referred_by=referred_by,
        device_id=device_id or None,
        signup_ip=signup_ip,
    )

    # Generate unique referral code (USERNAME + random numbers style)
    base_name = (fullname or email or '').strip().upper().replace(' ', '')
    if not base_name:
        base_name = f"USER{phone[-4:]}" if phone else "USER"
    base_name = base_name[:8]

    from random import randint
    for _ in range(10):
        suffix = str(randint(1000, 9999))
        code = f"{base_name}{suffix}"
        if not User.objects.filter(referral_code=code).exists():
            user.referral_code = code
            break

    user.save()
    otp_obj.delete()

    # Create referral record if applicable
    if referred_by:
        # Daily referral limit per referrer
        today = timezone.now().date()
        from backend.models import WalletTransaction  # imported to keep admin/usage consistent
        todays_count = Referral.objects.filter(
            referrer=referred_by,
            created_at__date=today,
        ).exclude(status=Referral.STATUS_REJECTED).count()

        is_suspicious = False
        fraud_reason = ''

        if max_referrals_per_day and todays_count >= max_referrals_per_day:
            is_suspicious = True
            fraud_reason = 'Daily referral limit exceeded'

        # Device/IP based basic fraud flags
        if device_id:
            existing_same_device = User.objects.filter(device_id=device_id).exclude(id=user.id).count()
            if existing_same_device >= 1:
                is_suspicious = True
                fraud_reason = (fraud_reason + '; ' if fraud_reason else '') + 'Multiple accounts on same device'

        if signup_ip:
            existing_same_ip = User.objects.filter(signup_ip=signup_ip).exclude(id=user.id).count()
            if existing_same_ip >= 3:
                is_suspicious = True
                fraud_reason = (fraud_reason + '; ' if fraud_reason else '') + 'Too many signups from same IP'

        referral_record = Referral.objects.create(
            referrer=referred_by,
            referred_user=user,
            referral_code=referral_code_input,
            reward_amount=reward_amount,
            status=Referral.STATUS_PENDING if not is_suspicious else Referral.STATUS_REJECTED,
            is_suspicious=is_suspicious,
            fraud_reason=fraud_reason,
            device_id=device_id or '',
            signup_ip=signup_ip,
        )
        log.info('[Referral] create_account: Referral created id=%s, referrer=%s, referred=%s, status=%s',
                 referral_record.id, referred_by.email, user.email, referral_record.status)

        # Push notification to referrer when a friend signs up with their code
        try:
            from backend.fcm_utils import send_fcm_to_user
            friend_name = user.fullname or user.email or user.phone or 'Your friend'
            friend_name = str(friend_name)[:50]
            reward_str = str(int(reward_amount)) if reward_amount else '100'
            send_fcm_to_user(
                referred_by,
                'New referral joined 🎉',
                f'{friend_name} signed up using your referral code. You will earn ₹{reward_str} after their first order is delivered.',
                data={'screen': 'referral', 'type': 'referral_signup'},
            )
        except Exception as e:
            log.warning('[Referral] create_account: failed to send signup push: %s', e)

    return token_response(user, fcmtoken)


@api_view(['POST'])
def login(request):
    email = request.data.get('email')
    phone = request.data.get('phone')
    password = request.data.get('password')
    fcmtoken = request.data.get('fcmtoken')

    if email:
        user = get_object_or_404(User, email=email)
    elif phone:
        user = get_object_or_404(User, phone=phone)
    else:
        return Response('data_missing', 400)

    if check_password(password, user.password):
        return token_response(user, fcmtoken)
    else:
        return Response('incorrect password', 400)


@api_view(['GET'])
@permission_classes([AllowAny])  # Allow anyone to logout
def logout(request):
    """
    FIXED: Null-safe logout for all users
    GET /api/logout/?logout_all=true
    """
    try:
        # Get token safely
        token_header = request.headers.get('Authorization', '')

        # Remove 'Bearer ' or 'token ' prefix
        token_value = token_header.replace('Bearer ', '').replace('token ', '').strip()

        if not token_value:
            return Response({
                'success': True,
                'message': 'Already logged out'
            })

        # Check logout_all parameter
        logout_all_param = request.GET.get('logout_all', 'false').lower()
        logout_all = logout_all_param == 'true'

        # Try to get user from token
        try:
            token_obj = Token.objects.select_related('user').filter(token=token_value).first()

            if token_obj and token_obj.user:
                user = token_obj.user

                if logout_all:
                    # Delete all tokens for this user
                    deleted_count = Token.objects.filter(user=user).delete()[0]
                    print(f"Deleted {deleted_count} tokens for user {user.email}")
                else:
                    # Delete only this token
                    token_obj.delete()
                    print(f"Deleted token: {token_value[:20]}...")
            else:
                # Token doesn't exist or has no user - just delete if found
                Token.objects.filter(token=token_value).delete()
                print(f"Deleted orphan token")

        except Exception as token_error:
            print(f"Token deletion error: {token_error}")
            # Continue anyway - not critical

        return Response({
            'success': True,
            'message': 'Logged out successfully'
        })

    except Exception as e:
        print(f"Logout error: {str(e)}")
        import traceback
        traceback.print_exc()

        # CRITICAL: Even if error, return success to allow logout
        return Response({
            'success': True,
            'message': 'Logged out (with errors)'
        })

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def userdata(request):
    user = request.user
    data = UserSerializer(user, many=False).data
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def save_device_token(request):
    """
    Save or update FCM token for the authenticated user (for push notifications).
    POST /api/save-device-token/
    Body: {"fcm_token": "..."}
    """
    fcm_token = request.data.get('fcm_token') or request.data.get('fcmtoken')
    if not fcm_token or not str(fcm_token).strip():
        return Response(
            {'success': False, 'message': 'fcm_token is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    fcm_token = str(fcm_token).strip()
    user = request.user
    print(f'📲 Save device token: received for user {user.id} ({user.email}), token length={len(fcm_token)}')
    UserDevice.objects.update_or_create(
        user=user,
        fcm_token=fcm_token,
        defaults={'updated_at': timezone.now()},
    )
    count = UserDevice.objects.filter(user=user).count()
    print(f'📲 Device token saved for user {user.id} ({user.email}), total devices: {count}')
    return Response({'success': True, 'message': 'Device token saved', 'devices_count': count})


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def device_token_status(request):
    """
    Debug: GET /api/device-token-status/ - returns how many FCM devices are registered for the current user.
    If 0, push notifications will not be sent until the customer opens the app (so save-device-token runs).
    """
    count = UserDevice.objects.filter(user=request.user).count()
    return Response({'devices_count': count, 'user_id': request.user.id, 'email': request.user.email})


def _send_accept_push_notification(user, order_id):
    """
    Send FCM push to customer when vendor accepts order.
    """
    print(f'📲 Push: Called for order_id={order_id} user_id={user.id} ({user.email})')
    try:
        tokens = list(
            UserDevice.objects.filter(user=user)
            .values_list('fcm_token', flat=True)
        )
        print(f'📲 Push: Found {len(tokens)} device token(s) for user {user.id}')
        if not tokens:
            print(f'📲 Push: No FCM tokens for user {user.id} ({user.email}) - customer should open app and ensure notifications are allowed')
            return
        import firebase_admin
        from firebase_admin import messaging
        try:
            firebase_admin.get_app()
        except ValueError:
            print('📲 Push: Firebase Admin not initialized - set FIREBASE_ADMIN_CREDENTIALS in settings and restart server')
            return
        android_config = messaging.AndroidConfig(priority='high')
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title='Booking Confirmed 🎉',
                body='Your order has been accepted',
            ),
            data={
                'screen': 'orders',
                'orderId': str(order_id),
                'type': 'order_accepted',
            },
            android=android_config,
            tokens=tokens,
        )
        batch = messaging.send_each_for_multicast(message)
        print(f'📲 Push sent: {batch.success_count} success, {batch.failure_count} failure for user {user.id}')
        if batch.failure_count > 0:
            for i, err in enumerate(batch.responses):
                if not err.success:
                    print(f'📲 Push token error {i}: {err.exception}')
    except Exception as e:
        print(f'📲 Push notification error: {e}')
        import traceback
        traceback.print_exc()


def _send_reject_push_notification(user, order_id):
    """
    Send FCM push to customer when vendor rejects order.
    """
    print(f'📲 Push (reject): Called for order_id={order_id} user_id={user.id} ({user.email})')
    try:
        tokens = list(
            UserDevice.objects.filter(user=user)
            .values_list('fcm_token', flat=True)
        )
        print(f'📲 Push (reject): Found {len(tokens)} device token(s) for user {user.id}')
        if not tokens:
            print(f'📲 Push (reject): No FCM tokens for user {user.id} ({user.email})')
            return
        import firebase_admin
        from firebase_admin import messaging
        try:
            firebase_admin.get_app()
        except ValueError:
            print('📲 Push (reject): Firebase Admin not initialized')
            return
        android_config = messaging.AndroidConfig(priority='high')
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title='Order Rejected',
                body='Your order has been rejected by the vendor.',
            ),
            data={
                'screen': 'orders',
                'orderId': str(order_id),
                'type': 'order_rejected',
            },
            android=android_config,
            tokens=tokens,
        )
        batch = messaging.send_each_for_multicast(message)
        print(f'📲 Push (reject) sent: {batch.success_count} success, {batch.failure_count} failure for user {user.id}')
        if batch.failure_count > 0:
            for i, err in enumerate(batch.responses):
                if not err.success:
                    print(f'📲 Push (reject) token error {i}: {err.exception}')
    except Exception as e:
        print(f'📲 Push (reject) notification error: {e}')
        import traceback
        traceback.print_exc()


# NEW: Slides endpoint
@api_view(['GET'])
@permission_classes([AllowAny])
def slides(request):
    """
    Get all promotional slides ordered by position
    Public endpoint - no authentication required
    """
    try:
        slides_list = Slide.objects.all().order_by('position')

        slides_data = []
        for slide in slides_list:
            try:
                image_url = None
                if slide.image:
                    image_url = request.build_absolute_uri(slide.image.url) if request else slide.image.url

                slide_data = {
                    'id': slide.id,
                    'position': slide.position,
                    'image': image_url,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id}: {e}")
                continue

        return Response(slides_data, status=200)

    except Exception as e:
        print(f"Error fetching slides: {e}")
        return Response([], status=200)


# ---------------------------------------------------------------------------
# Home Banners (dynamic home page banners between Search Bar and Rent/Services)
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AllowAny])
def home_banners(request):
    """
    GET /api/home-banners
    Return only active banners, ordered by display_order ASC.
    Public endpoint for customer app.
    """
    try:
        banners = HomeBanner.objects.filter(
            is_active=True,
            deleted_at__isnull=True,
        ).order_by('display_order', 'id')

        data = []
        for b in banners:
            image_url = None
            if b.image:
                image_url = request.build_absolute_uri(b.image.url) if request else b.image.url
            data.append({
                'id': b.id,
                'title': b.title or '',
                'image_url': image_url,
                'redirect_type': b.redirect_type,
                'redirect_value': b.redirect_value or '',
            })
        return Response(data, status=200)
    except Exception as e:
        logger.exception("Error fetching home banners: %s", e)
        return Response([], status=200)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticatedUser])
@parser_classes([MultiPartParser, FormParser])
def admin_home_banner_list_create(request):
    """
    GET: List all banners (including inactive). POST: Create banner.
    Admin API - requires authenticated user (Bearer token).
    """
    if request.method == 'GET':
        banners = HomeBanner.objects.filter(deleted_at__isnull=True).order_by('display_order', 'id')
        data = []
        for b in banners:
            image_url = None
            if b.image:
                image_url = request.build_absolute_uri(b.image.url) if request else b.image.url
            data.append({
                'id': b.id,
                'title': b.title,
                'image_url': image_url,
                'redirect_type': b.redirect_type,
                'redirect_value': b.redirect_value,
                'display_order': b.display_order,
                'is_active': b.is_active,
                'created_at': b.created_at.isoformat() if b.created_at else None,
                'updated_at': b.updated_at.isoformat() if b.updated_at else None,
            })
        return Response(data, status=200)

    # POST
    image = request.FILES.get('image')
    if not image:
        return Response({'success': False, 'message': 'Banner image is required'}, status=400)
    if image.size > 5 * 1024 * 1024:
        return Response({'success': False, 'message': 'Image too large (max 5MB)'}, status=400)

    redirect_type = request.data.get('redirect_type', HomeBanner.REDIRECT_CATEGORY)
    if redirect_type not in [HomeBanner.REDIRECT_PRODUCT, HomeBanner.REDIRECT_CATEGORY, HomeBanner.REDIRECT_EXTERNAL]:
        redirect_type = HomeBanner.REDIRECT_CATEGORY
    display_order = request.data.get('display_order')
    try:
        display_order = int(display_order) if display_order is not None else 0
    except (ValueError, TypeError):
        display_order = 0
    is_active = request.data.get('is_active')
    if isinstance(is_active, bool):
        pass
    elif isinstance(is_active, str):
        is_active = is_active.lower() in ('true', '1', 'yes')
    else:
        is_active = True

    try:
        banner = HomeBanner.objects.create(
            title=request.data.get('title') or None,
            image=image,
            redirect_type=redirect_type,
            redirect_value=request.data.get('redirect_value') or None,
            display_order=display_order,
            is_active=is_active,
        )
        image_url = request.build_absolute_uri(banner.image.url) if banner.image else None
        return Response({
            'success': True,
            'message': 'Banner created',
            'banner': {
                'id': banner.id,
                'title': banner.title,
                'image_url': image_url,
                'redirect_type': banner.redirect_type,
                'redirect_value': banner.redirect_value,
                'display_order': banner.display_order,
                'is_active': banner.is_active,
            },
        }, status=201)
    except Exception as e:
        logger.exception("Error creating home banner: %s", e)
        return Response({'success': False, 'message': str(e)}, status=500)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticatedUser])
@parser_classes([MultiPartParser, FormParser])
def admin_home_banner_detail(request, banner_id):
    """
    GET: Retrieve one banner. PUT: Update. DELETE: Soft delete.
    Admin API - requires authenticated user.
    """
    try:
        banner = HomeBanner.objects.get(id=banner_id, deleted_at__isnull=True)
    except HomeBanner.DoesNotExist:
        return Response({'success': False, 'message': 'Banner not found'}, status=404)

    if request.method == 'GET':
        image_url = request.build_absolute_uri(banner.image.url) if banner.image else None
        return Response({
            'id': banner.id,
            'title': banner.title,
            'image_url': image_url,
            'redirect_type': banner.redirect_type,
            'redirect_value': banner.redirect_value,
            'display_order': banner.display_order,
            'is_active': banner.is_active,
            'created_at': banner.created_at.isoformat() if banner.created_at else None,
            'updated_at': banner.updated_at.isoformat() if banner.updated_at else None,
        }, status=200)

    if request.method == 'DELETE':
        from django.utils import timezone
        banner.deleted_at = timezone.now()
        banner.save()
        return Response({'success': True, 'message': 'Banner deleted'}, status=200)

    # PUT
    if request.FILES.get('image'):
        banner.image = request.FILES['image']
    if 'title' in request.data:
        banner.title = request.data.get('title') or None
    if 'redirect_type' in request.data:
        rt = request.data['redirect_type']
        if rt in [HomeBanner.REDIRECT_PRODUCT, HomeBanner.REDIRECT_CATEGORY, HomeBanner.REDIRECT_EXTERNAL]:
            banner.redirect_type = rt
    if 'redirect_value' in request.data:
        banner.redirect_value = request.data.get('redirect_value') or None
    if 'display_order' in request.data:
        try:
            banner.display_order = int(request.data['display_order'])
        except (ValueError, TypeError):
            pass
    if 'is_active' in request.data:
        val = request.data['is_active']
        banner.is_active = val if isinstance(val, bool) else str(val).lower() in ('true', '1', 'yes')

    try:
        banner.save()
        image_url = request.build_absolute_uri(banner.image.url) if banner.image else None
        return Response({
            'success': True,
            'message': 'Banner updated',
            'banner': {
                'id': banner.id,
                'title': banner.title,
                'image_url': image_url,
                'redirect_type': banner.redirect_type,
                'redirect_value': banner.redirect_value,
                'display_order': banner.display_order,
                'is_active': banner.is_active,
            },
        }, status=200)
    except Exception as e:
        logger.exception("Error updating home banner: %s", e)
        return Response({'success': False, 'message': str(e)}, status=500)


# Enhanced Home Screen API with slides integration
# views.py - Update home_screen_data function

# views.py - UPDATED: Guest-friendly endpoints

from rest_framework.permissions import AllowAny


# ✅ FIXED: Home screen now works for guests

# views.py - ✅ FIXED: Proper None-safe guest handling

from rest_framework.permissions import AllowAny


# ✅ FIXED: Home screen - None-safe guest handling
@api_view(['GET'])
@permission_classes([AllowAny])
def home_screen_data(request):
    """
    Enhanced home screen with home page items
    Query params: pincode (optional)
    ✅ NOW WORKS FOR GUESTS
    """
    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    if not is_authenticated:
        # ✅ Guest mode - create mock user for compatibility
        print("🎭 Guest mode detected")
        from django.contrib.auth.models import AnonymousUser
        user = AnonymousUser()
        user.email = 'guest@beautyhub.com'
        user.cart = type('Cart', (), {'count': lambda: 0})()
        user.wishlist = type('Wishlist', (), {'count': lambda: 0})()
    else:
        print(f"👤 Authenticated user: {user.email}")

    pincode = request.GET.get('pincode')

    print(f"\n{'=' * 60}")
    print(f"🏠 HOME SCREEN REQUEST")
    print(f"{'=' * 60}")
    print(f"📍 Pincode: {pincode}")
    print(f"👤 User: {user.email if hasattr(user, 'email') else 'Guest'}")
    print(f"🎭 Guest Mode: {not is_authenticated}")

    # Check serviceability
    is_serviceable = False
    location_info = None
    serviceability_message = "Please select your location"

    if pincode:
        from backend.utils import check_pincode_serviceability
        is_serviceable, location_info, serviceability_message = check_pincode_serviceability(pincode)
        print(f"✅ Serviceable: {is_serviceable}")
        if location_info:
            print(f"📍 Location: {location_info.area_name}, {location_info.city}")

    # If not serviceable, return minimal data
    if pincode and not is_serviceable:
        print(f"⚠️ Location not serviceable: {pincode}")
        print(f"{'=' * 60}\n")

        # ✅ Guest-friendly response
        guest_user_data = {
            'email': 'guest@beautyhub.com',
            'phone': '',
            'fullname': 'Guest User',
            'notifications': 0,
            'wishlist': [],
            'cart': [],
        } if not is_authenticated else UserSerializer(user, many=False).data

        return Response({
            'is_serviceable': False,
            'message': serviceability_message,
            'pincode': pincode,
            'user': guest_user_data,
            'promotional_slides': [],
            'categories': [],
            'home_page_items': [],
            'recommended_products': [],
            'cart_count': 0,
            'wishlist_count': 0,
        })

    # Get user data
    if is_authenticated:
        user_data = UserSerializer(user, many=False).data
    else:
        # ✅ Guest user data
        user_data = {
            'email': 'guest@beautyhub.com',
            'phone': '',
            'fullname': 'Guest User',
            'notifications': 0,
            'wishlist': [],
            'cart': [],
        }

    # ✅ Get ALL categories
    print(f"\n📊 FETCHING CATEGORIES...")

    if location_info:
        from backend.utils import get_available_categories_for_pincode
        categories = get_available_categories_for_pincode(pincode)
        print(f"📍 Using pincode-based categories")
    else:
        from backend.models import Category
        categories = Category.objects.all().order_by('position')
        print(f"🌍 Using all categories (no pincode)")

    print(f"✅ Found {categories.count()} categories")

    # Serialize categories
    categories_data = CategorySerializer(categories, many=True, context={'request': request}).data

    # Get promotional slides
    try:
        slides = Slide.objects.all().order_by('position')
        slides_data = []
        for slide in slides:
            try:
                image_url = None
                if slide.image:
                    image_url = request.build_absolute_uri(slide.image.url) if request else slide.image.url
                slide_data = {
                    'id': slide.id,
                    'position': slide.position,
                    'image': image_url,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id}: {e}")
                continue
        print(f"✅ Loaded {len(slides_data)} slides")
    except Exception as e:
        print(f"Error fetching slides: {e}")
        slides_data = []

    # Get Home Page Items
    try:
        if pincode:
            home_page_items = HomePageItem.objects.filter(
                is_active=True
            ).filter(
                Q(show_in_all_locations=True) |
                Q(specific_locations__pincode=pincode, specific_locations__is_active=True)
            ).distinct().order_by('item_type', 'position')
        else:
            home_page_items = HomePageItem.objects.filter(
                is_active=True,
                show_in_all_locations=True
            ).order_by('item_type', 'position')

        home_page_items_data = HomePageItemSerializer(
            home_page_items,
            many=True,
            context={'request': request}
        ).data

        print(f"✅ Loaded {len(home_page_items_data)} home page items")
    except Exception as e:
        print(f"❌ Error loading home page items: {e}")
        home_page_items_data = []

    # Get recommended products
    recommended_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(options_set__quantity__gt=0).order_by('position', '-created_at')[:6]

    products_data = []
    for product in recommended_products:
        first_option = product.options_set.first()
        first_image = None
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'position': getattr(product, 'position', 9999),
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    # Get cart and wishlist counts
    cart_count = user.cart.count() if is_authenticated else 0
    wishlist_count = user.wishlist.count() if is_authenticated else 0

    print(f"\n{'=' * 60}")
    print(f"📦 FINAL RESPONSE")
    print(f"✅ Categories: {len(categories_data)}")
    print(f"✅ Products: {len(products_data)}")
    print(f"🛒 Cart: {cart_count} | ❤️ Wishlist: {wishlist_count}")
    print(f"{'=' * 60}\n")

    return Response({
        'is_serviceable': True,
        'location_info': {
            'pincode': location_info.pincode,
            'area_name': location_info.area_name,
            'city': location_info.city,
            'state': location_info.state,
            'rent_available': location_info.rent_available,
            'service_available': location_info.service_available,
            'delivery_charge': location_info.delivery_charge,
            'delivery_time': location_info.delivery_time,
        } if location_info else None,
        'message': serviceability_message,
        'user': user_data,
        'categories': categories_data,
        'promotional_slides': slides_data,
        'home_page_items': home_page_items_data,
        'recommended_products': products_data,
        'cart_count': cart_count,
        'wishlist_count': wishlist_count,
        'unread_notifications': user_data.get('notifications', 0)
    })


# ✅ FIXED: Wishlist - None-safe guest handling
@api_view(['GET'])
@permission_classes([AllowAny])
def get_wishlist_items_enhanced(request):
    """
    Get enhanced wishlist items
    ✅ FIXED: Properly handles both guests and authenticated users
    """
    # ✅ FIXED: Simpler authentication check for custom User model
    user = getattr(request, 'user', None)
    is_authenticated = (
            user is not None and
            hasattr(user, 'id') and
            hasattr(user, 'email') and
            user.email != 'guest@beautyhub.com'  # Exclude guest users
    )

    # Debug logging
    print(f"\n{'=' * 60}")
    print(f"📋 WISHLIST REQUEST")
    print(f"{'=' * 60}")
    if is_authenticated:
        print(f"✅ Authenticated User: {user.email}")
        print(f"   User ID: {user.id}")
    else:
        print(f"🎭 Guest or unauthenticated user")
    print(f"{'=' * 60}\n")

    if not is_authenticated:
        print("🎭 Returning empty wishlist for guest/unauthenticated user")
        return Response({
            'success': True,
            'wishlist_items': [],
            'total_items': 0,
            'message': 'Please login to view your wishlist'
        })

    try:
        # ✅ Get wishlist items
        wishlist_items = user.wishlist.select_related(
            'product__category'
        ).prefetch_related(
            'images_set'
        ).all()

        print(f"📦 Found {wishlist_items.count()} wishlist items for {user.email}")

        wishlist_data = []
        for idx, item in enumerate(wishlist_items, 1):
            try:
                print(f"\n  Processing item {idx}/{wishlist_items.count()}:")
                print(f"    Product: {item.product.title}")
                print(f"    Option: {item.option}")

                # Get first image
                first_image = item.images_set.first()
                image_url = None
                if first_image and first_image.image:
                    try:
                        image_url = request.build_absolute_uri(first_image.image.url)
                        print(f"    ✅ Image URL: {image_url[:50]}...")
                    except Exception as e:
                        print(f"    ⚠️ Image URL error: {e}")

                # Calculate ratings
                total_ratings = (
                        item.product.star_5 + item.product.star_4 +
                        item.product.star_3 + item.product.star_2 + item.product.star_1
                )
                average_rating = 0
                if total_ratings > 0:
                    average_rating = round(
                        ((item.product.star_5 * 5) + (item.product.star_4 * 4) +
                         (item.product.star_3 * 3) + (item.product.star_2 * 2) +
                         (item.product.star_1 * 1)) / total_ratings, 1
                    )

                # Calculate discount
                discount_percentage = 0
                if item.product.offer_price > 0 and item.product.offer_price < item.product.price:
                    discount_percentage = round(
                        ((item.product.price - item.product.offer_price) / item.product.price) * 100
                    )

                # ✅ Get rental pricing safely
                rent_for_1_day = 0
                offer_price_per_day = 0
                rental_label = ""

                try:
                    # Try to get rental pricing from ProductOption methods
                    if hasattr(item, 'get_rental_price'):
                        rent_for_1_day = item.get_rental_price('1_day') or 0

                    if hasattr(item, 'get_price_per_day'):
                        offer_price_per_day = item.get_price_per_day('1_day') or 0

                    # If methods don't exist, try direct field access
                    if rent_for_1_day == 0 and hasattr(item, 'option_rent_1_day'):
                        rent_for_1_day = item.option_rent_1_day or 0

                    if offer_price_per_day == 0 and rent_for_1_day > 0:
                        offer_price_per_day = rent_for_1_day

                    if offer_price_per_day > 0:
                        rental_label = f"Rent for 1 day: ₹{int(offer_price_per_day)}"
                        print(f"    💰 Rental price: ₹{offer_price_per_day}/day")

                except Exception as e:
                    print(f"    ⚠️ Rental pricing error: {e}")

                # Build wishlist item data
                item_data = {
                    'id': str(item.id),
                    'product_id': str(item.product.id),
                    'product_option_id': str(item.id),
                    'name': f"({item.option}) {item.product.title}" if item.option else item.product.title,
                    'image': image_url,
                    'price': float(item.product.price),
                    'original_price': float(item.product.price),
                    'offer_price': float(item.product.offer_price) if item.product.offer_price else 0,
                    'discount_percentage': discount_percentage,
                    'rating': average_rating,
                    'reviews': total_ratings,
                    'in_stock': item.quantity > 0,
                    'category': item.product.category.name if item.product.category else 'Other',
                    'cod_available': item.product.cod,
                    'delivery_charge': float(item.product.delivery_charge) if item.product.delivery_charge else 0,
                    'quantity_available': item.quantity,
                    'created_at': item.product.created_at.isoformat(),
                    'rent_for_1_day': rent_for_1_day,
                    'offer_price_per_day': offer_price_per_day,
                    'label': rental_label,
                    'in_cart': user.cart.filter(id=item.id).exists(),
                }

                wishlist_data.append(item_data)
                print(f"    ✅ Item added to response")

            except Exception as e:
                print(f"    ❌ Error serializing item {item.id}: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

        print(f"\n{'=' * 60}")
        print(f"✅ RETURNING {len(wishlist_data)} WISHLIST ITEMS")
        print(f"{'=' * 60}\n")

        return Response({
            'success': True,
            'wishlist_items': wishlist_data,
            'total_items': len(wishlist_data),
            'message': 'Wishlist items fetched successfully'
        })

    except Exception as e:
        print(f"\n{'=' * 60}")
        print(f"❌ WISHLIST FETCH ERROR")
        print(f"{'=' * 60}")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'=' * 60}\n")

        return Response({
            'success': False,
            'message': f'Failed to fetch wishlist: {str(e)}',
            'wishlist_items': [],
            'total_items': 0
        }, status=500)


# ✅ FIXED: Service wishlist - None-safe guest handling
@api_view(['GET'])
@permission_classes([AllowAny])
def get_service_wishlist(request):
    """
    Get user's service wishlist
    ✅ FIXED: Works for guests
    """
    # ✅ FIXED: Better authentication check
    user = getattr(request, 'user', None)
    is_authenticated = (
        user is not None and
        hasattr(user, 'id') and
        hasattr(user, 'email') and
        user.email != 'guest@beautyhub.com'
    )

    if not is_authenticated:
        print("🎭 Guest accessing service wishlist - returning empty")
        return Response({
            'success': True,
            'wishlist_items': [],
            'total_items': 0,
            'message': 'Please login to view your service wishlist'
        })

    try:
        wishlist_items = ServiceWishlistItem.objects.filter(
            user=user
        ).select_related(
            'service', 'service_option'
        ).prefetch_related(
            'service_option__images_set'
        ).order_by('-added_at')

        serializer = ServiceWishlistItemSerializer(
            wishlist_items,
            many=True,
            context={'request': request}
        )

        return Response({
            'success': True,
            'wishlist_items': serializer.data,
            'total_items': wishlist_items.count()
        })

    except Exception as e:
        print(f'❌ Error fetching service wishlist: {str(e)}')
        return Response({
            'success': False,
            'message': 'Failed to fetch wishlist',
            'wishlist_items': []
        }, status=500)


# ✅ FIXED: Service categories - None-safe guest handling
@api_view(['GET'])
@permission_classes([AllowAny])
def get_service_categories(request):
    """
    Get service categories with serviceability check
    ✅ NOW WORKS FOR GUESTS
    """
    pincode = request.GET.get('pincode')

    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    print(f'🔧 Fetching service categories for pincode: {pincode}')
    print(f'🎭 Guest Mode: {not is_authenticated}')

    # Check serviceability
    is_serviceable = True
    serviceability_message = "Service categories loaded"

    if pincode:
        from backend.utils import check_pincode_serviceability
        is_serviceable, location_info, serviceability_message = check_pincode_serviceability(pincode)

        if not is_serviceable:
            return Response({
                'is_serviceable': False,
                'message': serviceability_message,
                'categories': [],
                'total_categories': 0
            })

        if location_info and not location_info.service_available:
            return Response({
                'is_serviceable': True,
                'message': 'Services are not available in your location yet',
                'categories': [],
                'total_categories': 0
            })

    try:
        if pincode:
            from backend.utils import get_available_service_categories_for_pincode
            categories = get_available_service_categories_for_pincode(pincode)
        else:
            categories = ServiceCategory.objects.all().order_by('position')

        print(f'✅ Found {categories.count()} service categories')

        categories_data = ServiceCategorySerializer(
            categories,
            many=True,
            context={'request': request}
        ).data

        return Response({
            'is_serviceable': True,
            'categories': categories_data,
            'total_categories': len(categories_data)
        }, status=200)

    except Exception as e:
        print(f'❌ Error: {str(e)}')
        return Response({
            'success': False,
            'message': 'Unable to load service categories',
            'categories': [],
        }, status=500)


# Bulk update product positions (category-wise ordering)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_product_positions(request):
    """
    POST /api/products/update-positions/
    Body: { "positions": [ {"id": "product-uuid", "position": 0}, ... ] }
    Updates position for each product. Ordering is category-wise (admin sends products of one category).
    """
    data = request.data
    if not isinstance(data, dict) or 'positions' not in data:
        return Response(
            {'success': False, 'error': 'Request body must include "positions" array of {id, position}'},
            status=400
        )
    positions_list = data['positions']
    if not isinstance(positions_list, list):
        return Response(
            {'success': False, 'error': '"positions" must be an array'},
            status=400
        )
    updates = []
    for item in positions_list:
        if not isinstance(item, dict) or 'id' not in item or 'position' not in item:
            continue
        try:
            pid = item['id']
            pos = int(item['position'])
            if pos < 0:
                pos = 0
            updates.append((pid, pos))
        except (ValueError, TypeError):
            continue
    if not updates:
        return Response(
            {'success': False, 'error': 'No valid {id, position} entries'},
            status=400
        )
    try:
        with transaction.atomic():
            for product_id, position in updates:
                Product.objects.filter(id=product_id).update(position=position)
        return Response({
            'success': True,
            'message': f'Updated positions for {len(updates)} product(s)',
            'updated_count': len(updates),
        })
    except Exception as e:
        return Response(
            {'success': False, 'error': str(e)},
            status=500
        )


# Search and filtering APIs
@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Allow guest access
def search_products(request):
    """
    🔍 Enhanced product search
    ✅ NOW WORKS FOR GUESTS

    Query Parameters:
        - q: Search query
        - category: Category ID filter
        - min_price: Minimum price
        - max_price: Maximum price
        - sort: Sorting (relevance, price_low_high, price_high_low, newest, popularity, discount)
        - page: Page number
    """
    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', None)
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    sort_by = request.GET.get('sort', 'relevance')
    page = request.GET.get('page', 1)

    print(f'🔍 Search query: "{query}" | Guest: {not is_authenticated}')

    # Base queryset
    products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(options_set__quantity__gt=0).distinct()

    # Search
    if query:
        products = products.filter(
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query) |
            Q(options_set__option__icontains=query)
        ).distinct()

    # Category filter
    if category_id:
        try:
            products = products.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    # Price filters
    if min_price:
        try:
            min_price_val = float(min_price)
            products = products.filter(
                Q(offer_price__gte=min_price_val, offer_price__gt=0) |
                Q(price__gte=min_price_val, offer_price__lte=0) |
                Q(price__gte=min_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    if max_price:
        try:
            max_price_val = float(max_price)
            products = products.filter(
                Q(offer_price__lte=max_price_val, offer_price__gt=0) |
                Q(price__lte=max_price_val, offer_price__lte=0) |
                Q(price__lte=max_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    # Sorting
    if sort_by == 'price_low_high':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('effective_price')
    elif sort_by == 'price_high_low':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('-effective_price')
    elif sort_by == 'newest':
        products = products.order_by('-created_at')
    elif sort_by == 'popularity':
        products = products.annotate(
            total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1')
        ).order_by('-total_ratings')
    elif sort_by == 'discount':
        products = products.annotate(
            discount_percent=Case(
                When(
                    offer_price__gt=0,
                    offer_price__lt=F('price'),
                    then=(F('price') - F('offer_price')) * 100.0 / F('price')
                ),
                default=0
            )
        ).order_by('-discount_percent')
    else:
        products = products.order_by('position', '-created_at')

    # Pagination
    paginator = Paginator(products, 20)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # ✅ Build product data with rental pricing
    products_data = []
    for product in page_obj:
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image:
                image_url = request.build_absolute_uri(first_image.image.url)

        # ✅ Get rental pricing from first option
        rent_price_1_day = 0
        rental_label = ""
        if first_option:
            rent_price_1_day = first_option.get_price_per_day('1_day') or 0
            rental_label = f"Rent for 1 day: ₹{int(rent_price_1_day)}"

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'position': getattr(product, 'position', 9999),
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'rent_price_1_day': rent_price_1_day,
            'rental_label': rental_label,
            'options': []
        }

        # ✅ Add each product option with rental pricing
        for option in product.options_set.all():
            option_serialized = ProductOptionSerializer(option, context={'request': request}).data
            rent_price = option.get_price_per_day('1_day') or 0
            option_serialized['rental_label'] = f"Rent for 1 day: ₹{int(rent_price)}"
            product_data['options'].append(option_serialized)

        products_data.append(product_data)

    print(f'✅ Found {len(products_data)} products')

    return Response({
        'products': products_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'query': query,
        'filters': {
            'category_id': category_id,
            'min_price': min_price,
            'max_price': max_price,
            'sort_by': sort_by,
        }
    })



# Cart and Wishlist APIs
@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_cart(request):
    user = request.user
    product_option_id = request.data.get('product_option_id')
    quantity = request.data.get('quantity', 1)

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if user.cart.filter(id=product_option_id).exists():
        return Response({'message': 'Product already in cart'}, status=200)

    if product_option.quantity < quantity:
        return Response({'error': 'Insufficient stock'}, status=400)

    user.cart.add(product_option)

    return Response({
        'message': 'Added to cart successfully',
        'cart_count': user.cart.count()
    })


# Replace the existing remove_from_cart function with this updated version

@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_wishlist(request):
    """
    ✅ FIXED: Add a product option to user's wishlist
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    print(f"❤️ Add to wishlist request:")
    print(f"   User: {user.email}")
    print(f"   Product Option ID: {product_option_id}")

    if not product_option_id:
        return Response({
            'success': False,
            'message': 'Product option ID is required',
            'wishlist_count': user.wishlist.count()
        }, status=400)

    try:
        product_option = ProductOption.objects.select_related('product').get(id=product_option_id)
        print(f"   ✅ Found product: {product_option.product.title}")
    except ProductOption.DoesNotExist:
        print(f"   ❌ Product option not found")
        return Response({
            'success': False,
            'message': 'Product option not found',
            'wishlist_count': user.wishlist.count()
        }, status=404)

    # Check if already in wishlist
    if user.wishlist.filter(id=product_option_id).exists():
        print(f"   ⚠️ Already in wishlist")
        return Response({
            'success': True,
            'message': 'Product already in wishlist',
            'wishlist_count': user.wishlist.count()
        }, status=200)

    # Add to wishlist
    user.wishlist.add(product_option)
    print(f"   ✅ Added to wishlist")

    # Verify it was added
    new_count = user.wishlist.count()
    print(f"   📊 New wishlist count: {new_count}")

    return Response({
        'success': True,
        'message': 'Added to wishlist successfully',
        'wishlist_count': new_count
    })

@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_wishlist(request, product_option_id):
    """
    Remove a product option from user's wishlist
    """
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product option not found',
            'wishlist_count': user.wishlist.count()  # ✅ ADD: Always return count
        }, status=404)

    if not user.wishlist.filter(id=product_option_id).exists():
        return Response({
            'success': False,
            'message': 'Product not in wishlist',
            'wishlist_count': user.wishlist.count()  # ✅ ADD: Always return count
        }, status=400)

    user.wishlist.remove(product_option)

    return Response({
        'success': True,
        'message': 'Removed from wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_cart_items(request):
    user = request.user
    cart_items = user.cart.select_related('product').prefetch_related('images_set').all()

    cart_data = []
    for item in cart_items:
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        item_data = {
            'id': str(item.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.price,
            'offer_price': item.product.offer_price,
            'quantity': item.quantity,
            'cod': item.product.cod,
            'delivery_charge': item.product.delivery_charge,
        }
        cart_data.append(item_data)

    total_amount = sum(item['price'] for item in cart_data)
    offer_amount = sum(item['offer_price'] for item in cart_data if item['offer_price'] > 0)
    delivery_charges = sum(item['delivery_charge'] for item in cart_data)

    final_amount = offer_amount if offer_amount > 0 else total_amount
    final_amount += delivery_charges

    return Response({
        'cart_items': cart_data,
        'total_amount': total_amount,
        'offer_amount': offer_amount,
        'delivery_charges': delivery_charges,
        'final_amount': final_amount,
        'total_items': len(cart_data)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_wishlist_items(request):
    user = request.user
    wishlist_items = user.wishlist.select_related('product').prefetch_related('images_set').all()

    wishlist_data = []
    for item in wishlist_items:
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        item_data = {
            'id': str(item.product.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.price,
            'offer_price': item.product.offer_price,
        }
        wishlist_data.append(item_data)

    return Response({
        'wishlist_items': wishlist_data,
        'total_items': len(wishlist_data)
    })




# ===================== 1. SEARCH PRODUCTS =====================
@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Allow guest access
def search_products(request):
    """
    🔍 Enhanced product search
    ✅ NOW WORKS FOR GUESTS

    Query Parameters:
        - q: Search query
        - category: Category ID filter
        - min_price: Minimum price
        - max_price: Maximum price
        - sort: Sorting (relevance, price_low_high, price_high_low, newest, popularity, discount)
        - page: Page number
    """
    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', None)
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    sort_by = request.GET.get('sort', 'relevance')
    page = request.GET.get('page', 1)

    print(f'🔍 Search query: "{query}" | Guest: {not is_authenticated}')

    # Base queryset
    products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(options_set__quantity__gt=0).distinct()

    # Search
    if query:
        products = products.filter(
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query) |
            Q(options_set__option__icontains=query)
        ).distinct()

    # Category filter
    if category_id:
        try:
            products = products.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    # Price filters
    if min_price:
        try:
            min_price_val = float(min_price)
            products = products.filter(
                Q(offer_price__gte=min_price_val, offer_price__gt=0) |
                Q(price__gte=min_price_val, offer_price__lte=0) |
                Q(price__gte=min_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    if max_price:
        try:
            max_price_val = float(max_price)
            products = products.filter(
                Q(offer_price__lte=max_price_val, offer_price__gt=0) |
                Q(price__lte=max_price_val, offer_price__lte=0) |
                Q(price__lte=max_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    # Sorting
    if sort_by == 'price_low_high':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('effective_price')
    elif sort_by == 'price_high_low':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('-effective_price')
    elif sort_by == 'newest':
        products = products.order_by('-created_at')
    elif sort_by == 'popularity':
        products = products.annotate(
            total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1')
        ).order_by('-total_ratings')
    elif sort_by == 'discount':
        products = products.annotate(
            discount_percent=Case(
                When(
                    offer_price__gt=0,
                    offer_price__lt=F('price'),
                    then=(F('price') - F('offer_price')) * 100.0 / F('price')
                ),
                default=0
            )
        ).order_by('-discount_percent')
    else:
        products = products.order_by('position', '-created_at')

    # Pagination
    paginator = Paginator(products, 20)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # ✅ Build product data with rental pricing
    products_data = []
    for product in page_obj:
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image:
                image_url = request.build_absolute_uri(first_image.image.url)

        # ✅ Get rental pricing from first option
        rent_price_1_day = 0
        rental_label = ""
        if first_option:
            rent_price_1_day = first_option.get_price_per_day('1_day') or 0
            rental_label = f"Rent for 1 day: ₹{int(rent_price_1_day)}"

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'position': getattr(product, 'position', 9999),
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'rent_price_1_day': rent_price_1_day,
            'rental_label': rental_label,
            'options': []
        }

        # ✅ Add each product option with rental pricing
        for option in product.options_set.all():
            option_serialized = ProductOptionSerializer(option, context={'request': request}).data
            rent_price = option.get_price_per_day('1_day') or 0
            option_serialized['rental_label'] = f"Rent for 1 day: ₹{int(rent_price)}"
            product_data['options'].append(option_serialized)

        products_data.append(product_data)

    print(f'✅ Found {len(products_data)} products')

    return Response({
        'products': products_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'query': query,
        'filters': {
            'category_id': category_id,
            'min_price': min_price,
            'max_price': max_price,
            'sort_by': sort_by,
        }
    })


# ===================== 2. CATEGORY PRODUCTS =====================
@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Allow guest access
def category_products(request, category_id):
    """
    Enhanced category products with filtering and sorting
    ✅ NOW WORKS FOR GUESTS

    URL: /api/category/<category_id>/products/
    Query Parameters:
        - sort: Sorting option
        - min_price: Minimum price
        - max_price: Maximum price
        - page: Page number
    """
    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    print(f'📂 Category {category_id} products | Guest: {not is_authenticated}')

    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return Response({'error': 'Category not found'}, status=404)

    sort_by = request.GET.get('sort', 'relevance')
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    page = request.GET.get('page', 1)

    # Include products that have at least one option with quantity > 0 OR have no options yet
    has_option_with_stock = ProductOption.objects.filter(
        product_id=OuterRef('pk'), quantity__gt=0
    )
    has_any_option = ProductOption.objects.filter(product_id=OuterRef('pk'))
    products = (
        Product.objects.select_related('category')
        .prefetch_related('options_set__images_set')
        .filter(category=category)
        .filter(Exists(has_option_with_stock) | ~Exists(has_any_option))
        .distinct()
    )

    # Apply price filters
    if min_price:
        try:
            min_price_val = float(min_price)
            products = products.filter(
                Q(offer_price__gte=min_price_val, offer_price__gt=0) |
                Q(price__gte=min_price_val, offer_price__lte=0) |
                Q(price__gte=min_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    if max_price:
        try:
            max_price_val = float(max_price)
            products = products.filter(
                Q(offer_price__lte=max_price_val, offer_price__gt=0) |
                Q(price__lte=max_price_val, offer_price__lte=0) |
                Q(price__lte=max_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    # Apply sorting
    if sort_by == 'price_low_high':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('effective_price')
    elif sort_by == 'price_high_low':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('-effective_price')
    elif sort_by == 'newest':
        products = products.order_by('-created_at')
    elif sort_by == 'popularity':
        products = products.annotate(
            total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1')
        ).order_by('-total_ratings')
    elif sort_by == 'discount':
        products = products.annotate(
            discount_percent=Case(
                When(
                    offer_price__gt=0,
                    offer_price__lt=F('price'),
                    then=(F('price') - F('offer_price')) * 100.0 / F('price')
                ),
                default=0
            )
        ).order_by('-discount_percent')
    elif sort_by == 'rating':
        products = products.annotate(
            total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1'),
            avg_rating=Case(
                When(
                    total_ratings__gt=0,
                    then=(F('star_5') * 5 + F('star_4') * 4 + F('star_3') * 3 + F('star_2') * 2 + F('star_1') * 1) / F(
                        'total_ratings')
                ),
                default=0
            )
        ).order_by('-avg_rating')
    else:
        products = products.order_by('position', '-created_at')

    # Pagination
    paginator = Paginator(products, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # ✅ Build product data
    products_data = []
    for product in page_obj:
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'position': getattr(product, 'position', 9999),
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        # ✅ Add options with rental pricing
        for option in product.options_set.all():
            option_serialized = ProductOptionSerializer(option, context={'request': request}).data
            product_data['options'].append(option_serialized)

        products_data.append(product_data)

    category_data = CategorySerializer(category, context={'request': request}).data

    print(f'✅ Loaded {len(products_data)} products from category')

    return Response({
        'category': category_data,
        'products': products_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'filters': {
            'min_price': min_price,
            'max_price': max_price,
            'sort_by': sort_by,
        }
    })


# Add missing all_categories endpoint for ViewAll screen
@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Allow guest access
def all_categories(request):
    """
    Get all categories with product counts for categories view
    ✅ NOW WORKS FOR GUESTS

    URL: /api/categories/
    Query Parameters:
        - page: Page number
    """
    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    print(f'📚 Loading all categories | Guest: {not is_authenticated}')

    page = request.GET.get('page', 1)

    # Get categories with product counts
    categories = Category.objects.annotate(
        product_count=Count('products_set', distinct=True),
        in_stock_count=Count(
            'products_set__options_set',
            filter=Q(products_set__options_set__quantity__gt=0),
            distinct=True
        )
    ).filter(in_stock_count__gt=0).order_by('position', 'name')

    # Pagination
    paginator = Paginator(categories, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Prepare category data for ViewAll screen (formatted as products)
    categories_data = []
    for category in page_obj:
        category_data = {
            'id': str(category.id),
            'title': category.name,
            'image': request.build_absolute_uri(category.image.url) if category.image and request else (
                category.image.url if category.image else None),
            'price': 0,
            'offer_price': 0,
            'category_data': {
                'id': category.id,
                'name': category.name,
                'image': request.build_absolute_uri(category.image.url) if category.image and request else (
                    category.image.url if category.image else None),
                'product_count': category.in_stock_count,
            }
        }
        categories_data.append(category_data)

    print(f'✅ Loaded {len(categories_data)} categories')

    return Response({
        'products': categories_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
    })


# ===================== 4. PAGE ITEM PRODUCTS =====================
@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Allow guest access
def page_item_products(request):
    """
    Get products from specific page items with filtering and sorting
    ✅ NOW WORKS FOR GUESTS

    URL: /api/page-item-products/
    Query Parameters:
        - title: Page item title
        - category_id: Category ID
        - sort: Sorting option
        - min_price: Minimum price
        - max_price: Maximum price
        - page: Page number
    """
    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    page_item_title = request.GET.get('title', '')
    category_id = request.GET.get('category_id', None)
    sort_by = request.GET.get('sort', 'relevance')
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    page = request.GET.get('page', 1)

    print(f'📄 Page items: "{page_item_title}" | Guest: {not is_authenticated}')

    # Start with empty queryset
    products = Product.objects.none()

    # Find page items by title and/or category
    page_items_query = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product__options_set__images_set'
    )

    if page_item_title:
        page_items_query = page_items_query.filter(title__icontains=page_item_title)

    if category_id:
        try:
            page_items_query = page_items_query.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    page_items = page_items_query.all()

    # Collect all product IDs from page items
    product_ids = set()
    for page_item in page_items:
        for option in page_item.product_options.all():
            product_ids.add(option.product.id)

    if product_ids:
        # Get products with stock
        products = Product.objects.select_related('category').prefetch_related(
            'options_set__images_set'
        ).filter(
            id__in=product_ids,
            options_set__quantity__gt=0
        ).distinct()

        # Apply price filters
        if min_price:
            try:
                min_price_val = float(min_price)
                products = products.filter(
                    Q(offer_price__gte=min_price_val, offer_price__gt=0) |
                    Q(price__gte=min_price_val, offer_price__lte=0) |
                    Q(price__gte=min_price_val, offer_price__isnull=True)
                )
            except (ValueError, TypeError):
                pass

        if max_price:
            try:
                max_price_val = float(max_price)
                products = products.filter(
                    Q(offer_price__lte=max_price_val, offer_price__gt=0) |
                    Q(price__lte=max_price_val, offer_price__lte=0) |
                    Q(price__lte=max_price_val, offer_price__isnull=True)
                )
            except (ValueError, TypeError):
                pass

        # Apply sorting
        if sort_by == 'price_low_high':
            products = products.annotate(
                effective_price=Case(
                    When(offer_price__gt=0, then=F('offer_price')),
                    default=F('price')
                )
            ).order_by('effective_price')
        elif sort_by == 'price_high_low':
            products = products.annotate(
                effective_price=Case(
                    When(offer_price__gt=0, then=F('offer_price')),
                    default=F('price')
                )
            ).order_by('-effective_price')
        elif sort_by == 'newest':
            products = products.order_by('-created_at')
        elif sort_by == 'discount':
            products = products.annotate(
                discount_percent=Case(
                    When(
                        offer_price__gt=0,
                        offer_price__lt=F('price'),
                        then=(F('price') - F('offer_price')) * 100.0 / F('price')
                    ),
                    default=0
                )
            ).order_by('-discount_percent')
        elif sort_by == 'rating':
            products = products.annotate(
                total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1'),
                avg_rating=Case(
                    When(
                        total_ratings__gt=0,
                        then=(F('star_5') * 5 + F('star_4') * 4 + F('star_3') * 3 + F('star_2') * 2 + F(
                            'star_1') * 1) / F('total_ratings')
                    ),
                    default=0
                )
            ).order_by('-avg_rating')
        else:
            products = products.order_by('position', '-created_at')

    # Pagination
    paginator = Paginator(products, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Enhanced product serialization
    products_data = []
    for product in page_obj:
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'position': getattr(product, 'position', 9999),
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        # Add options
        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    print(f'✅ Loaded {len(products_data)} products from page items')

    return Response({
        'products': products_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'page_item_title': page_item_title,
        'category_id': category_id,
        'filters': {
            'min_price': min_price,
            'max_price': max_price,
            'sort_by': sort_by,
        }
    })


# Add missing page_item_products endpoint
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def page_item_products(request):
    """
    Get products from specific page items with filtering and sorting
    """
    page_item_title = request.GET.get('title', '')
    category_id = request.GET.get('category_id', None)
    sort_by = request.GET.get('sort', 'relevance')
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    page = request.GET.get('page', 1)

    # Start with empty queryset
    products = Product.objects.none()

    # Find page items by title and/or category
    page_items_query = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product__options_set__images_set'
    )

    if page_item_title:
        page_items_query = page_items_query.filter(title__icontains=page_item_title)

    if category_id:
        try:
            page_items_query = page_items_query.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    page_items = page_items_query.all()

    # Collect all product IDs from page items
    product_ids = set()
    for page_item in page_items:
        for option in page_item.product_options.all():
            product_ids.add(option.product.id)

    if product_ids:
        # Get products with stock
        products = Product.objects.select_related('category').prefetch_related(
            'options_set__images_set'
        ).filter(
            id__in=product_ids,
            options_set__quantity__gt=0
        ).distinct()

        # Apply price filters
        if min_price:
            try:
                min_price_val = float(min_price)
                products = products.filter(
                    Q(offer_price__gte=min_price_val, offer_price__gt=0) |
                    Q(price__gte=min_price_val, offer_price__lte=0) |
                    Q(price__gte=min_price_val, offer_price__isnull=True)
                )
            except (ValueError, TypeError):
                pass

        if max_price:
            try:
                max_price_val = float(max_price)
                products = products.filter(
                    Q(offer_price__lte=max_price_val, offer_price__gt=0) |
                    Q(price__lte=max_price_val, offer_price__lte=0) |
                    Q(price__lte=max_price_val, offer_price__isnull=True)
                )
            except (ValueError, TypeError):
                pass

        # Apply sorting
        if sort_by == 'price_low_high':
            products = products.annotate(
                effective_price=Case(
                    When(offer_price__gt=0, then=F('offer_price')),
                    default=F('price')
                )
            ).order_by('effective_price')
        elif sort_by == 'price_high_low':
            products = products.annotate(
                effective_price=Case(
                    When(offer_price__gt=0, then=F('offer_price')),
                    default=F('price')
                )
            ).order_by('-effective_price')
        elif sort_by == 'newest':
            products = products.order_by('-created_at')
        elif sort_by == 'discount':
            products = products.annotate(
                discount_percent=Case(
                    When(
                        offer_price__gt=0,
                        offer_price__lt=F('price'),
                        then=(F('price') - F('offer_price')) * 100.0 / F('price')
                    ),
                    default=0
                )
            ).order_by('-discount_percent')
        elif sort_by == 'rating':
            products = products.annotate(
                total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1'),
                avg_rating=Case(
                    When(
                        total_ratings__gt=0,
                        then=(F('star_5') * 5 + F('star_4') * 4 + F('star_3') * 3 + F('star_2') * 2 + F(
                            'star_1') * 1) / F('total_ratings')
                    ),
                    default=0
                )
            ).order_by('-avg_rating')
        else:
            products = products.order_by('position', '-created_at')

    # Pagination
    paginator = Paginator(products, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Enhanced product serialization
    products_data = []
    for product in page_obj:
        # Get first option and its first image
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'position': getattr(product, 'position', 9999),
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        # Add options
        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    return Response({
        'products': products_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'page_item_title': page_item_title,
        'category_id': category_id,
        'filters': {
            'min_price': min_price,
            'max_price': max_price,
            'sort_by': sort_by,
        }
    })


#


# Fixed Cart and Wishlist Management APIs
@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_cart(request):
    """
    Add a product option to user's cart
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')
    quantity = request.data.get('quantity', 1)

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    # Check if product is already in cart
    if user.cart.filter(id=product_option_id).exists():
        return Response({'message': 'Product already in cart'}, status=200)

    # Check stock availability
    if product_option.quantity < quantity:
        return Response({'error': 'Insufficient stock'}, status=400)

    # Add to cart
    user.cart.add(product_option)

    return Response({
        'message': 'Added to cart successfully',
        'cart_count': user.cart.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_cart(request, item_id):  # Ã¢Å“â€¦ Changed parameter name
    """
    Remove a cart item - works with both CartItem ID and product_option_id
    """
    user = request.user

    print(f"Ã°Å¸â€Â¥ Attempting to remove cart item: {item_id}")
    print(f"Ã°Å¸â€Â¥ User: {request.user.email if hasattr(request.user, 'email') else 'Unknown'}")

    try:
        # First, try to find it as a CartItem ID
        cart_item = CartItem.objects.filter(id=item_id, user=user).first()

        if cart_item:
            print(f"Ã¢Å“â€¦ Found as CartItem: {cart_item}")
            # Delete the CartItem
            product_option = cart_item.product_option
            cart_item.delete()

            # Also remove from the old ManyToMany cart if it exists
            if user.cart.filter(id=product_option.id).exists():
                user.cart.remove(product_option)

            return Response({
                'success': True,
                'message': 'Removed from cart successfully',
                'cart_count': CartItem.objects.filter(user=user).count()
            })

        # If not found as CartItem, try as product_option_id (backward compatibility)
        print(f"Ã¢Å¡ Ã¯Â¸Â Not found as CartItem, trying as ProductOption ID")
        try:
            product_option = ProductOption.objects.get(id=item_id)

            # Remove all CartItems with this product_option
            deleted_count, _ = CartItem.objects.filter(
                user=user,
                product_option=product_option
            ).delete()

            print(f"Ã°Å¸â€”â€˜Ã¯Â¸Â Deleted {deleted_count} CartItems")

            # Also remove from old ManyToMany cart
            if user.cart.filter(id=item_id).exists():
                user.cart.remove(product_option)

            if deleted_count > 0 or user.cart.filter(id=item_id).exists():
                return Response({
                    'success': True,
                    'message': 'Removed from cart successfully',
                    'cart_count': CartItem.objects.filter(user=user).count()
                })
            else:
                return Response({
                    'success': False,
                    'error': 'Item not found in cart'
                }, status=404)

        except ProductOption.DoesNotExist:
            print(f"Ã¢ÂÅ’ ProductOption not found with ID: {item_id}")
            return Response({
                'success': False,
                'error': 'Item not found in cart'
            }, status=404)

    except Exception as e:
        print(f"Ã°Å¸â€™Â¥ Error removing from cart: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'error': f'Failed to remove from cart: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_wishlist(request):
    """
    Add a product option to user's wishlist
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    # Check if product is already in wishlist
    if user.wishlist.filter(id=product_option_id).exists():
        return Response({'message': 'Product already in wishlist'}, status=200)

    # Add to wishlist
    user.wishlist.add(product_option)

    return Response({
        'message': 'Added to wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_wishlist(request, product_option_id):
    """
    Remove a product option from user's wishlist
    """
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if not user.wishlist.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in wishlist'}, status=400)

    user.wishlist.remove(product_option)

    return Response({
        'message': 'Removed from wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_cart_items(request):
    """
    Get user's cart items with enhanced product data
    """
    user = request.user
    cart_items = user.cart.select_related('product').prefetch_related('images_set').all()

    cart_data = []
    for item in cart_items:
        # Get first image
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        item_data = {
            'id': str(item.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.price,
            'offer_price': item.product.offer_price,
            'quantity': item.quantity,
            'cod': item.product.cod,
            'delivery_charge': item.product.delivery_charge,
        }
        cart_data.append(item_data)

    # Calculate totals
    total_amount = sum(item['price'] for item in cart_data)
    offer_amount = sum(item['offer_price'] for item in cart_data if item['offer_price'] > 0)
    delivery_charges = sum(item['delivery_charge'] for item in cart_data)

    final_amount = offer_amount if offer_amount > 0 else total_amount
    final_amount += delivery_charges

    return Response({
        'cart_items': cart_data,
        'total_amount': total_amount,
        'offer_amount': offer_amount,
        'delivery_charges': delivery_charges,
        'final_amount': final_amount,
        'total_items': len(cart_data)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_wishlist_items(request):
    """
    Get user's wishlist items with enhanced product data
    """
    user = request.user
    wishlist_items = user.wishlist.select_related('product').prefetch_related('images_set').all()

    wishlist_data = []
    for item in wishlist_items:
        # Get first image
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        item_data = {
            'id': str(item.product.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.price,
            'offer_price': item.product.offer_price,
        }
        wishlist_data.append(item_data)

    return Response({
        'wishlist_items': wishlist_data,
        'total_items': len(wishlist_data)
    })


# Missing view functions that were referenced in original code
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_page_items_by_category(request, category_id):
    """
    Get page items for a specific category
    """
    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return Response({'error': 'Category not found'}, status=404)

    page_items = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product',
        'product_options__images_set'
    ).filter(category=category).order_by('position')

    page_items_data = []
    for page_item in page_items:
        # Get product options with their details
        product_options_data = []
        for option in page_item.product_options.all():
            first_image = option.images_set.first()
            image_url = None
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

            # Use option rent price/offer for card (Option price 200, Option offer 150), not product/buy price
            price_val = option.get_price() or 0
            offer_val = option.get_offer_price() or 0
            effective_price = offer_val if (offer_val > 0 and price_val and offer_val < price_val) else price_val
            cutted_price = price_val if (offer_val > 0 and price_val and offer_val < price_val) else None
            discount_percentage = round(((price_val - effective_price) / price_val) * 100) if cutted_price and price_val > 0 else 0

            product_data = {
                'id': str(option.product.id),
                'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                'price': price_val,
                'offer_price': offer_val,
                'effective_price': effective_price,
                'cutted_price': cutted_price,
                'discount_percentage': discount_percentage,
                'option_price': option.option_price if option.option_price > 0 else None,
                'buy_price': option.get_buy_price(),
                'rental_price_per_day': option.get_rental_price('1_day'),
                'image': image_url,
            }
            product_options_data.append(product_data)

        page_item_data = {
            'id': page_item.id,
            'title': page_item.title,
            'position': page_item.position,
            'viewtype': page_item.viewtype,
            'image': request.build_absolute_uri(page_item.image.url) if page_item.image and request else (
                page_item.image.url if page_item.image else None),
            'category': page_item.category.name,
            'category_id': page_item.category.id,
            'product_options': product_options_data
        }
        page_items_data.append(page_item_data)

    category_data = CategorySerializer(category, context={'request': request}).data

    return Response({
        'category': category_data,
        'page_items': page_items_data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_categories_with_page_items(request):
    """
    Get all categories with their page items
    """
    categories = Category.objects.prefetch_related(
        'pageitems_set__product_options__product',
        'pageitems_set__product_options__images_set'
    ).order_by('position')

    categories_data = []
    for category in categories:
        # Get page items for this category
        page_items = category.pageitems_set.order_by('position')
        page_items_data = []

        for page_item in page_items:
            # Get product options with their details
            product_options_data = []
            for option in page_item.product_options.all()[:8]:  # Limit to 8 products per page item
                first_image = option.images_set.first()
                image_url = None
                if first_image and request:
                    image_url = request.build_absolute_uri(first_image.image.url)
                elif first_image:
                    image_url = first_image.image.url

                price_val = option.get_price() or 0
                offer_val = option.get_offer_price() or 0
                effective_price = offer_val if (offer_val > 0 and price_val and offer_val < price_val) else price_val
                cutted_price = price_val if (offer_val > 0 and price_val and offer_val < price_val) else None
                discount_percentage = round(((price_val - effective_price) / price_val) * 100) if cutted_price and price_val > 0 else 0

                product_data = {
                    'id': str(option.product.id),
                    'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                    'price': price_val,
                    'offer_price': offer_val,
                    'effective_price': effective_price,
                    'cutted_price': cutted_price,
                    'discount_percentage': discount_percentage,
                    'option_price': option.option_price if option.option_price > 0 else None,
                    'buy_price': option.get_buy_price(),
                    'rental_price_per_day': option.get_rental_price('1_day'),
                    'image': image_url,
                }
                product_options_data.append(product_data)

            page_item_data = {
                'id': page_item.id,
                'title': page_item.title,
                'position': page_item.position,
                'viewtype': page_item.viewtype,
                'image': request.build_absolute_uri(page_item.image.url) if page_item.image and request else (
                    page_item.image.url if page_item.image else None),
                'category': page_item.category.name,
                'category_id': page_item.category.id,
                'product_options': product_options_data
            }
            page_items_data.append(page_item_data)

        category_data = {
            'id': category.id,
            'name': category.name,
            'position': category.position,
            'image': request.build_absolute_uri(category.image.url) if category.image and request else (
                category.image.url if category.image else None),
            'page_items': page_items_data
        }
        categories_data.append(category_data)

    return Response({
        'categories': categories_data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def product_details(request, product_id):
    """
    Get detailed product information including images, options, ratings, and user interaction status
    """
    try:
        product = Product.objects.select_related('category').prefetch_related(
            'options_set__images_set'
        ).get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=404)

    user = request.user

    # Get all product options with their images
    options_data = []
    for option in product.options_set.all():
        # Get all images for this option
        option_images = []
        for img in option.images_set.order_by('position'):
            img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
            option_images.append({
                'position': img.position,
                'image': img_url,
                'product_option_id': str(option.id)
            })

        option_data = {
            'id': str(option.id),
            'option': option.option,
            'quantity': option.quantity,
            'in_stock': option.quantity > 0,
            'images': option_images,
            # Check if this option is in user's cart or wishlist
            'in_cart': user.cart.filter(id=option.id).exists(),
            'in_wishlist': user.wishlist.filter(id=option.id).exists(),
        }
        options_data.append(option_data)

    # Calculate ratings and reviews summary
    total_ratings = product.star_5 + product.star_4 + product.star_3 + product.star_2 + product.star_1

    if total_ratings > 0:
        average_rating = (
                                 (product.star_5 * 5) +
                                 (product.star_4 * 4) +
                                 (product.star_3 * 3) +
                                 (product.star_2 * 2) +
                                 (product.star_1 * 1)
                         ) / total_ratings
        average_rating = round(average_rating, 1)
    else:
        average_rating = 0

    # Calculate discount percentage
    discount_percentage = 0
    if product.offer_price > 0 and product.offer_price < product.price:
        discount_percentage = round(((product.price - product.offer_price) / product.price) * 100)

    # Get the first available option's first image as main product image
    main_image = None
    first_option = product.options_set.first()
    if first_option:
        first_image = first_option.images_set.first()
        if first_image and request:
            main_image = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            main_image = first_image.image.url

    # Check if any option is in stock
    in_stock = product.options_set.filter(quantity__gt=0).exists()

    # Prepare product data
    product_data = {
        'id': str(product.id),
        'title': product.title,
        'description': product.description,
        'price': product.price,
        'offer_price': product.offer_price,
        'cutted_price': product.price if product.offer_price > 0 else None,  # Original price when there's an offer
        'effective_price': product.offer_price if product.offer_price > 0 else product.price,
        'delivery_charge': product.delivery_charge,
        'cod_available': product.cod,
        'discount_percentage': discount_percentage,
        'main_image': main_image,
        'category': {
            'id': product.category.id,
            'name': product.category.name,
        } if product.category else None,
        'in_stock': in_stock,
        'created_at': product.created_at.isoformat(),
        'updated_at': product.updated_at.isoformat(),

        # Ratings and Reviews
        'ratings': {
            'average_rating': average_rating,
            'total_reviews': total_ratings,
            'star_5': product.star_5,
            'star_4': product.star_4,
            'star_3': product.star_3,
            'star_2': product.star_2,
            'star_1': product.star_1,
        },

        # Product Options with Images
        'options': options_data,

        # User Interaction Status
        'user_status': {
            'has_in_cart': any(option['in_cart'] for option in options_data),
            'has_in_wishlist': any(option['in_wishlist'] for option in options_data),
        }
    }

    # Get related products from the same category (optional)
    related_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        category=product.category,
        options_set__quantity__gt=0
    ).exclude(
        id=product.id
    ).distinct()[:6]

    related_products_data = []
    for related_product in related_products:
        # Get first option and its first image for related product
        first_option = related_product.options_set.first()
        image_url = None
        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        related_data = {
            'id': str(related_product.id),
            'title': related_product.title,
            'price': related_product.price,
            'offer_price': related_product.offer_price,
            'image': image_url,
        }
        related_products_data.append(related_data)

    return Response({
        'product': product_data,
        'related_products': related_products_data,
        'success': True
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def apply_coupon(request):
    """
    Apply a coupon code and get discount. Uses Coupon model (admin-created).

    POST /api/cart/apply-coupon/
    Request: {
        "coupon_code": "FIRST50",
        "cart_total": 1500,
        "products": ["uuid1", "uuid2"],   # optional: product_option ids in cart
        "product_ids": ["uuid1", "uuid2"], # optional: product ids (if not using products)
        "services": ["uuid1"]              # optional: service ids if cart has services
    }
    Success: { "success": true, "discount": 200, "final_total": 1300, "message": "...", "coupon_code": "..." }
    Invalid: { "success": false, "message": "Invalid or expired coupon code" }
    """
    from backend.utils import validate_coupon_and_calculate_discount

    user = request.user
    data = request.data
    coupon_code = data.get('coupon_code', '').strip()
    cart_total = data.get('cart_total')

    if cart_total is None:
        # Fallback: compute cart total from user's CartItem
        cart_items = CartItem.objects.filter(user=user).select_related(
            'product_option', 'product_option__product'
        )
        cart_total = 0
        product_option_ids = []
        for item in cart_items:
            cart_total += (item.rental_price or 0) * item.quantity
            product_option_ids.append(str(item.product_option_id))
    else:
        cart_total = int(cart_total)
        product_option_ids = data.get('products') or []
        if product_option_ids and isinstance(product_option_ids[0], str):
            product_option_ids = [pid.strip() for pid in product_option_ids if pid]

    product_ids = data.get('product_ids') or []
    if product_ids and isinstance(product_ids[0], str):
        product_ids = [pid.strip() for pid in product_ids if pid]
    service_ids = data.get('services') or []
    if service_ids and isinstance(service_ids[0], str):
        service_ids = [sid.strip() for sid in service_ids if sid]

    success, message, discount_amount, final_total, coupon_obj = validate_coupon_and_calculate_discount(
        coupon_code=coupon_code,
        user=user,
        cart_total=cart_total,
        product_option_ids=product_option_ids or None,
        product_ids=product_ids or None,
        service_ids=service_ids or None,
    )

    if success:
        return Response({
            'success': True,
            'discount': discount_amount,
            'final_total': final_total,
            'message': message,
            'coupon_code': (coupon_obj.code if coupon_obj else coupon_code),
        })
    return Response({
        'success': False,
        'message': message or 'Invalid or expired coupon code',
    }, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def bulk_update_cart(request):
    """
    Update multiple cart items at once
    """
    user = request.user
    updates = request.data.get('updates', [])

    if not updates:
        return Response({'error': 'No updates provided'}, status=400)

    try:
        with transaction.atomic():
            for update in updates:
                product_option_id = update.get('product_option_id')
                quantity = update.get('quantity', 0)

                if not product_option_id:
                    continue

                try:
                    product_option = ProductOption.objects.get(id=product_option_id)
                except ProductOption.DoesNotExist:
                    continue

                if quantity <= 0:
                    # Remove item from cart
                    user.cart.remove(product_option)
                else:
                    # Check stock availability
                    if product_option.quantity < quantity:
                        return Response({
                            'error': f'Insufficient stock for {product_option.product.title}. Only {product_option.quantity} available.'
                        }, status=400)

                    # Add/update item in cart (since Django ManyToMany doesn't support quantity directly,
                    # you might need to create a separate CartItem model for quantities)
                    if not user.cart.filter(id=product_option_id).exists():
                        user.cart.add(product_option)

        # Return updated cart data
        return get_cart_items(request)

    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def cart_summary(request):
    """
    Get cart summary with item count and total value
    """
    user = request.user
    cart_items = user.cart.select_related('product').all()

    if not cart_items:
        return Response({
            'total_items': 0,
            'total_amount': 0,
            'cart_count': 0
        })

    total_amount = 0
    offer_amount = 0

    for item in cart_items:
        if item.product.offer_price > 0:
            offer_amount += item.product.offer_price
        else:
            total_amount += item.product.price

    final_amount = offer_amount if offer_amount > 0 else total_amount

    return Response({
        'total_items': len(cart_items),
        'total_amount': total_amount,
        'offer_amount': offer_amount,
        'final_amount': final_amount,
        'cart_count': len(cart_items)
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def clear_cart(request):
    """
    âœ… FIXED: Clear all items from user's cart (both CartItem and ManyToMany)
    """
    user = request.user

    try:
        # âœ… FIX: Delete all CartItem entries for this user
        cart_items_count = CartItem.objects.filter(user=user).count()
        CartItem.objects.filter(user=user).delete()

        # Also clear the old ManyToMany cart (for backward compatibility)
        user.cart.clear()

        print(f"âœ… Cleared {cart_items_count} items from cart for user {user.email}")

        return Response({
            'success': True,
            'message': f'Cart cleared successfully. Removed {cart_items_count} items.',
            'cart_count': 0
        })

    except Exception as e:
        print(f"âŒore"
              f" Error clearing cart: {str(e)}")
        import traceback
        traceback.print_exc()

        return Response({
            'success': False,
            'message': f'Failed to clear cart: {str(e)}',
            'cart_count': user.cart.count()
        }, status=500)

@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def move_to_wishlist(request):
    """
    âœ… UPGRADED: Add item to wishlist while keeping it in cart
    Does NOT remove from cart - just bookmarks the item
    """
    user = request.user
    item_id = request.data.get('product_option_id')

    print(f"â¤ï¸ Add to wishlist request:")
    print(f"  - Item ID: {item_id}")
    print(f"  - User: {user.email}")

    if not item_id:
        return Response({
            'success': False,
            'message': 'Item ID is required'
        }, status=400)

    try:
        product_option = None
        cart_item = None

        # âœ… Try to find as CartItem first
        try:
            cart_item = CartItem.objects.select_related('product_option').get(
                id=item_id,
                user=user
            )
            product_option = cart_item.product_option
            print(f"âœ… Found as CartItem: {cart_item.id}")
        except CartItem.DoesNotExist:
            # âœ… If not CartItem, try as product_option_id
            try:
                product_option = ProductOption.objects.get(id=item_id)
                print(f"âœ… Found as ProductOption: {product_option.id}")
            except ProductOption.DoesNotExist:
                return Response({
                    'success': False,
                    'message': 'Product not found'
                }, status=404)

        if not product_option:
            return Response({
                'success': False,
                'message': 'Product not found'
            }, status=404)

        # âœ… Check if already in wishlist
        if user.wishlist.filter(id=product_option.id).exists():
            print(f"âš ï¸ Already in wishlist: {product_option.id}")
            return Response({
                'success': True,
                'message': 'Item already in wishlist',
                'cart_count': CartItem.objects.filter(user=user).count(),
                'wishlist_count': user.wishlist.count(),
                'already_in_wishlist': True
            })

        # âœ… Add to wishlist (KEEP IN CART)
        user.wishlist.add(product_option)
        print(f"â¤ï¸ Added to wishlist: {product_option.id}")
        print(f"ðŸ›’ Item remains in cart")

        # Get updated counts
        cart_count = CartItem.objects.filter(user=user).count()
        wishlist_count = user.wishlist.count()

        print(f"ðŸ“Š Updated counts - Cart: {cart_count}, Wishlist: {wishlist_count}")

        return Response({
            'success': True,
            'message': 'Added to wishlist',
            'cart_count': cart_count,
            'wishlist_count': wishlist_count,
            'already_in_wishlist': False
        })

    except Exception as e:
        print(f"âŒ Error adding to wishlist: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to add to wishlist: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def validate_cart(request):
    """
    Validate cart items for stock availability and price changes
    """
    user = request.user
    cart_items = user.cart.select_related('product').all()

    if not cart_items:
        return Response({
            'valid': True,
            'message': 'Cart is empty',
            'issues': []
        })

    issues = []

    for item in cart_items:
        # Check if product is still available
        if item.quantity <= 0:
            issues.append({
                'product_option_id': str(item.id),
                'product_title': item.product.title,
                'issue': 'out_of_stock',
                'message': 'This item is now out of stock'
            })

        # Check if price has changed (you can implement price tracking if needed)
        # This would require storing original price when added to cart

    return Response({
        'valid': len(issues) == 0,
        'message': 'Cart validation complete' if len(issues) == 0 else f'{len(issues)} issues found',
        'issues': issues,
        'total_issues': len(issues)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_cart_items_enhanced(request):
    """
    ✅ FIXED: Get enhanced cart items with complete null safety for rental pricing
    """
    user = request.user

    print(f"\n{'=' * 60}")
    print(f"🛒 CART REQUEST FOR: {user.email}")
    print(f"{'=' * 60}")

    try:
        # Get CartItem entries with related data
        cart_items_db = CartItem.objects.select_related(
            'product_option__product__category'
        ).prefetch_related(
            'product_option__images_set'
        ).filter(user=user).order_by('-created_at')

        print(f"📦 Found {cart_items_db.count()} cart items")

        cart_data = []
        total_amount = 0
        offer_amount = 0
        total_savings = 0
        delivery_charges = 0
        total_security_amount = 0

        for idx, cart_item in enumerate(cart_items_db, 1):
            print(f"\n  Processing item {idx}/{cart_items_db.count()}:")

            product_option = cart_item.product_option
            product = product_option.product

            print(f"    Product: {product.title}")
            print(f"    Option: {product_option.option}")
            print(f"    Rental Type: {cart_item.rental_type}")
            print(f"    Duration: {cart_item.rental_duration}")

            # Get first image with null safety
            first_image = product_option.images_set.first()
            image_url = None
            if first_image and first_image.image:
                try:
                    image_url = request.build_absolute_uri(first_image.image.url)
                    print(f"    ✅ Image URL: {image_url[:50]}...")
                except Exception as e:
                    print(f"    ⚠️ Image URL error: {e}")

            # ✅ Calculate prices with complete null safety
            original_price = int(product.price) if product.price else 0

            # ✅ CRITICAL: Calculate rental_price with multiple fallbacks
            rental_price = 0

            try:
                # Priority 1: Use stored cart_item rental_price
                if cart_item.rental_price and cart_item.rental_price > 0:
                    rental_price = int(cart_item.rental_price)
                    print(f"    ✅ Using stored rental price: ₹{rental_price}")

                # Priority 2: Calculate from product option
                elif cart_item.rental_type == 'rent' and cart_item.rental_duration:
                    calculated_price = product_option.get_rental_price(cart_item.rental_duration)
                    if calculated_price and calculated_price > 0:
                        rental_price = int(calculated_price)
                        print(f"    💡 Calculated rental price: ₹{rental_price}")

                # Priority 3: Use buy pricing
                elif cart_item.rental_type == 'buy':
                    buy_offer = product_option.get_buy_offer_price()
                    buy_price = product_option.get_buy_price()

                    if buy_offer and buy_offer > 0:
                        rental_price = int(buy_offer)
                        print(f"    💰 Using buy offer price: ₹{rental_price}")
                    elif buy_price and buy_price > 0:
                        rental_price = int(buy_price)
                        print(f"    💰 Using buy price: ₹{rental_price}")

                # Priority 4: Use product offer_price
                if rental_price == 0 and product.offer_price and product.offer_price > 0:
                    rental_price = int(product.offer_price)
                    print(f"    💡 Using product offer price: ₹{rental_price}")

                # Priority 5: Final fallback to original price
                if rental_price == 0:
                    rental_price = original_price
                    print(f"    ⚠️ Using original price as fallback: ₹{rental_price}")

            except Exception as e:
                print(f"    ❌ Rental price calculation error: {e}")
                # ✅ Ultimate fallback
                rental_price = original_price
                print(f"    🔄 Fallback to original price: ₹{rental_price}")

            # ✅ Ensure rental_price is never zero or null
            if not rental_price or rental_price <= 0:
                rental_price = original_price
                print(f"    ⚠️ Rental price was invalid, using: ₹{rental_price}")

            # Calculate savings
            savings_per_item = original_price - rental_price if rental_price < original_price else 0
            print(f"    💵 Savings per item: ₹{savings_per_item}")

            # Calculate rental end date with null safety
            rental_end_date = None
            if cart_item.rental_type == 'rent' and cart_item.selected_date:
                try:
                    from datetime import datetime, timedelta
                    start_date = datetime.strptime(str(cart_item.selected_date), '%Y-%m-%d')
                    duration_days = {
                        '1_day': 1, '2_days': 2, '3_days': 3,
                        '7_days': 7, '14_days': 14, '30_days': 30
                    }
                    days = duration_days.get(cart_item.rental_duration, 1)
                    end_date = start_date + timedelta(days=days - 1)
                    rental_end_date = end_date.strftime('%Y-%m-%d')
                    print(f"    📅 Rental period: {cart_item.selected_date} to {rental_end_date}")
                except Exception as e:
                    print(f"    ⚠️ Date calculation error: {e}")

            # Add to totals
            item_quantity = int(cart_item.quantity) if cart_item.quantity else 1
            total_amount += original_price * item_quantity
            offer_amount += rental_price * item_quantity
            total_savings += savings_per_item * item_quantity

            # Delivery charges
            item_delivery = int(product.delivery_charge) if product.delivery_charge else 0
            delivery_charges += item_delivery

            # Security amount (per product, × quantity)
            item_security = int(getattr(product, 'security_amount', 0) or 0)
            total_security_amount += item_security * item_quantity

            print(f"    🔢 Quantity: {item_quantity}")
            print(f"    💰 Item total: ₹{rental_price * item_quantity}")

            # ✅ Build item data with guaranteed non-null values
            item_data = {
                'id': str(cart_item.id),
                'product_option_id': str(product_option.id),
                'title': f"({product_option.option}) {product.title}" if product_option.option else product.title,
                'image': image_url,
                'price': original_price,
                'offer_price': int(
                    product.offer_price) if product.offer_price and product.offer_price > 0 else original_price,
                'quantity': item_quantity,
                'cod': bool(product.cod),
                'delivery_charge': item_delivery,
                'savings': savings_per_item,
                'product_id': str(product.id),
                'in_stock': product_option.quantity > 0,
                'stock_quantity': int(product_option.quantity) if product_option.quantity else 0,

                # ✅ Check wishlist status
                'in_wishlist': user.wishlist.filter(id=product_option.id).exists(),

                # ✅ Rental information with complete null safety
                'selected_date': cart_item.selected_date.strftime('%Y-%m-%d') if cart_item.selected_date else None,
                'rental_type': str(cart_item.rental_type) if cart_item.rental_type else 'buy',
                'rental_duration': str(cart_item.rental_duration) if cart_item.rental_duration else '',
                'rental_price': rental_price,  # ✅ Guaranteed to be valid int
                'rental_end_date': rental_end_date,
                'security_amount': item_security,
                'security_total': item_security * item_quantity,
            }

            cart_data.append(item_data)
            print(f"    ✅ Item added to cart response")

        # Calculate final amounts (include security deposit in total payable)
        final_amount = offer_amount + delivery_charges + total_security_amount

        # ✅ Free delivery logic
        free_delivery_threshold = 500
        free_delivery_eligible = offer_amount >= free_delivery_threshold
        amount_for_free_delivery = max(0, free_delivery_threshold - offer_amount)

        if free_delivery_eligible:
            delivery_charges = 0
            final_amount = offer_amount + total_security_amount
            print(f"\n🎉 FREE DELIVERY UNLOCKED!")
        else:
            print(f"\n💰 Add ₹{amount_for_free_delivery} more for free delivery")

        print(f"\n{'=' * 60}")
        print(f"📊 CART SUMMARY")
        print(f"{'=' * 60}")
        print(f"Items: {len(cart_data)}")
        print(f"Total Amount: ₹{total_amount}")
        print(f"Offer Amount: ₹{offer_amount}")
        print(f"Savings: ₹{total_savings}")
        print(f"Delivery: ₹{delivery_charges}")
        print(f"Final: ₹{final_amount}")
        print(f"{'=' * 60}\n")

        return Response({
            'success': True,
            'cart_items': cart_data,
            'summary': {
                'total_amount': int(total_amount),
                'offer_amount': int(offer_amount),
                'total_savings': int(total_savings),
                'delivery_charges': int(delivery_charges),
                'security_amount': int(total_security_amount),
                'final_amount': int(final_amount),
                'total_items': len(cart_data),
                'free_delivery_threshold': free_delivery_threshold,
                'free_delivery_eligible': free_delivery_eligible,
                'amount_for_free_delivery': int(amount_for_free_delivery)
            },
            'recommendations': {
                'similar_products': [],
                'frequently_bought_together': []
            }
        })

    except Exception as e:
        print(f"\n{'=' * 60}")
        print(f"❌ CART FETCH ERROR")
        print(f"{'=' * 60}")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'=' * 60}\n")

        return Response({
            'success': False,
            'message': f'Failed to fetch cart: {str(e)}',
            'cart_items': [],
            'summary': {
                'total_amount': 0,
                'offer_amount': 0,
                'total_savings': 0,
                'delivery_charges': 0,
                'security_amount': 0,
                'final_amount': 0,
                'total_items': 0,
                'free_delivery_threshold': 500,
                'free_delivery_eligible': False,
                'amount_for_free_delivery': 500
            }
        }, status=500)




# Add these to your views.py file

# Add this enhanced get_order_tracking to your views.py
# Replace the existing get_order_tracking function

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_order_tracking(request, order_id):
    """
    ✅ COMPLETE: Enhanced order tracking with booking confirmation status
    Shows expected delivery as user's selected date

    GET /api/orders/<order_id>/tracking/

    Returns:
        - Order details with expected_delivery showing selected date
        - Product list
        - Tracking timeline
        - Booking confirmation status
        - Support information
    """
    user = request.user

    try:
        order = Order.objects.select_related('user').get(id=order_id, user=user)
    except Order.DoesNotExist:
        return Response({'error': 'Order not found'}, status=404)

    # Get ordered products
    ordered_products = OrderedProduct.objects.select_related(
        'product_option__product'
    ).prefetch_related(
        'product_option__images_set'
    ).filter(order=order)

    # Get product bookings for this order
    product_bookings = ProductBooking.objects.filter(order=order).select_related('product')

    # Check booking statuses
    has_bookings = product_bookings.exists()
    booking_statuses = []
    all_bookings_confirmed = True
    any_booking_cancelled = False

    for booking in product_bookings:
        booking_statuses.append({
            'product_name': booking.product.title,
            'booking_date': booking.booking_date.strftime('%B %d, %Y'),
            'quantity': booking.quantity_booked,
            'status': booking.status,
            'status_display': booking.get_status_display()
        })

        if booking.status != 'CONFIRMED':
            all_bookings_confirmed = False
        if booking.status == 'CANCELLED':
            any_booking_cancelled = True

    # Determine overall booking status
    booking_confirmation_status = None
    if has_bookings:
        if all_bookings_confirmed:
            booking_confirmation_status = {
                'status': 'CONFIRMED',
                'message': '✅ All your bookings have been confirmed by the vendor!',
                'color': '#4ADE80'
            }
        elif any_booking_cancelled:
            booking_confirmation_status = {
                'status': 'CANCELLED',
                'message': '❌ Some bookings were cancelled. Please contact support for refund.',
                'color': '#FF0000'
            }
        else:
            booking_confirmation_status = {
                'status': 'PENDING',
                'message': '⏳ Awaiting vendor confirmation for your bookings.',
                'color': '#FFA500'
            }

    # Create tracking timeline
    tracking_steps = _generate_tracking_timeline(order, ordered_products)

    # Serialize ordered products
    products_data = []
    for ordered_product in ordered_products:
        first_image = ordered_product.product_option.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        product_data = {
            'id': str(ordered_product.id),
            'title': str(ordered_product.product_option),
            'image': image_url,
            'quantity': ordered_product.quantity,
            'price': ordered_product.product_price,
            'tx_price': ordered_product.tx_price,
            'status': ordered_product.status,
            'rating': ordered_product.rating,
        }
        products_data.append(product_data)

    # ✅ FIXED: Use stored expected_delivery or calculate
    expected_delivery = order.expected_delivery if hasattr(order,
                                                           'expected_delivery') and order.expected_delivery else _calculate_expected_delivery(
        order)

    response_data = {
        'order': {
            'id': str(order.id),
            'order_number': f"BH{str(order.id)[:8].upper()}",
            'status': order.tx_status,
            'payment_mode': order.payment_mode,
            'total_amount': order.tx_amount,
            'created_at': order.created_at.isoformat(),
            'delivery_address': order.address,
            'expected_delivery': expected_delivery,  # ✅ Shows user's selected date
        },
        'products': products_data,
        'tracking_timeline': tracking_steps,
        'support_info': {
            'phone': '+91 98765 43210',
            'email': 'support@beautyhub.com',
            'chat_available': True,
        }
    }

    # Add booking information if exists
    if has_bookings:
        response_data['booking_info'] = {
            'has_bookings': True,
            'confirmation_status': booking_confirmation_status,
            'bookings': booking_statuses,
            'total_bookings': len(booking_statuses)
        }

    return Response(response_data)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_user_orders(request):
    """
    ✅ COMPLETE: Get all orders for the authenticated user with vendor acceptance status
    Shows expected delivery as the user's selected date

    GET /api/orders/?page=1&status=SUCCESS

    Query Params:
        - page: Page number (default: 1)
        - status: Filter by status (PENDING, SUCCESS, CANCELLED, FAILED)

    Returns:
        - List of orders with expected_delivery showing user's selected date
        - Pagination info
        - Vendor confirmation status
        - Booking status
    """
    user = request.user
    page = request.GET.get('page', 1)
    status_filter = request.GET.get('status', None)

    # Get user's orders
    orders = Order.objects.select_related('user').prefetch_related(
        'orders_set__product_option__product',
        'orders_set__product_option__images_set'
    ).filter(user=user).order_by('-created_at')

    # Apply status filter if provided
    if status_filter:
        orders = orders.filter(tx_status=status_filter)
        print(f"📋 Filtering orders by status: {status_filter}")

    # Pagination
    paginator = Paginator(orders, 10)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    print(f"📦 Loading page {page_obj.number} of {paginator.num_pages}")

    orders_data = []
    for order in page_obj:
        # Get first product for display
        first_product = order.orders_set.first()
        product_image = None
        product_title = "Order Items"

        if first_product:
            product_title = str(first_product.product_option)
            first_image = first_product.product_option.images_set.first()
            if first_image and request:
                product_image = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                product_image = first_image.image.url

        # Calculate order summary
        total_items = order.orders_set.count()
        current_status = _get_current_tracking_status(order)

        # Check booking confirmation status
        has_bookings = ProductBooking.objects.filter(order=order).exists()
        booking_status = None
        vendor_confirmation_required = False

        if has_bookings:
            bookings = ProductBooking.objects.filter(order=order)
            all_confirmed = all(b.status == 'CONFIRMED' for b in bookings)
            any_cancelled = any(b.status == 'CANCELLED' for b in bookings)

            vendor_confirmation_required = True

            if all_confirmed:
                booking_status = 'CONFIRMED'
            elif any_cancelled:
                booking_status = 'CANCELLED'
            else:
                booking_status = 'PENDING'

        # ✅ FIXED: Use stored expected_delivery or calculate from rental dates
        expected_delivery = None

        # Priority 1: Use stored expected_delivery field
        if hasattr(order, 'expected_delivery') and order.expected_delivery:
            expected_delivery = order.expected_delivery
            print(f"  ✅ Using stored expected_delivery: {expected_delivery}")
        else:
            # Priority 2: Calculate from rental start date
            expected_delivery = _calculate_expected_delivery(order)
            print(f"  💡 Calculated expected_delivery: {expected_delivery}")

        order_data = {
            'id': str(order.id),
            'order_number': f"BH{str(order.id)[:8].upper()}",
            'status': order.tx_status,
            'current_status': current_status,
            'total_amount': order.tx_amount,
            'total_items': total_items,
            'created_at': order.created_at.isoformat(),
            'expected_delivery': expected_delivery,  # ✅ Shows user's selected date
            'product_preview': {
                'title': product_title,
                'image': product_image,
                'additional_items': max(0, total_items - 1)
            },
            # Vendor acceptance info
            'vendor_status': order.vendor_status if hasattr(order, 'vendor_status') else 'PENDING',
            'vendor_accepted_at': order.vendor_accepted_at.isoformat() if hasattr(order,
                                                                                  'vendor_accepted_at') and order.vendor_accepted_at else None,
            'vendor_confirmation_required': vendor_confirmation_required,
            'booking_status': booking_status,
        }
        orders_data.append(order_data)

    print(f"✅ Returning {len(orders_data)} orders")

    return Response({
        'orders': orders_data,
        'pagination': {
            'total_pages': paginator.num_pages,
            'current_page': page_obj.number,
            'total_orders': paginator.count,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def update_order_rating(request, ordered_product_id):
    """
    Update rating and review for a delivered product
    """
    user = request.user
    rating = request.data.get('rating', 0)
    review = request.data.get('review', '')

    if not (1 <= rating <= 5):
        return Response({'error': 'Rating must be between 1 and 5'}, status=400)

    try:
        ordered_product = OrderedProduct.objects.select_related('order').get(
            id=ordered_product_id,
            order__user=user,
            status='DELIVERED'
        )
    except OrderedProduct.DoesNotExist:
        return Response({'error': 'Product not found or not delivered'}, status=404)

    # Update rating and review
    from django.utils import timezone

    old_rating = ordered_product.rating
    ordered_product.rating = rating
    ordered_product.review_text = review  # ✅ Save review text
    ordered_product.rated_at = timezone.now()  # ✅ Save timestamp
    ordered_product.save()

    # Update product ratings
    product = ordered_product.product_option.product

    # Remove old rating if exists
    if old_rating > 0:
        if old_rating == 5:
            product.star_5 = max(0, product.star_5 - 1)
        elif old_rating == 4:
            product.star_4 = max(0, product.star_4 - 1)
        elif old_rating == 3:
            product.star_3 = max(0, product.star_3 - 1)
        elif old_rating == 2:
            product.star_2 = max(0, product.star_2 - 1)
        elif old_rating == 1:
            product.star_1 = max(0, product.star_1 - 1)

    # Add new rating
    if rating == 5:
        product.star_5 += 1
    elif rating == 4:
        product.star_4 += 1
    elif rating == 3:
        product.star_3 += 1
    elif rating == 2:
        product.star_2 += 1
    elif rating == 1:
        product.star_1 += 1

    product.save()

    return Response({
        'message': 'Rating updated successfully',
        'rating': rating,
        'review': review,  # ✅ Return review
        'product_id': str(product.id)
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def cancel_order(request, order_id):
    """
    Cancel an order if it's still cancellable
    """
    user = request.user
    cancellation_reason = request.data.get('reason', 'User requested cancellation')

    try:
        order = Order.objects.get(id=order_id, user=user)
    except Order.DoesNotExist:
        return Response({'error': 'Order not found'}, status=404)

    # Check if order is cancellable
    if order.tx_status not in ['INITIATED', 'PENDING', 'SUCCESS']:
        return Response({'error': 'Order cannot be cancelled at this stage'}, status=400)

    # Check if any products are already shipped
    shipped_products = order.orders_set.filter(status__in=['OUT_FOR_DELIVERY', 'DELIVERED'])
    if shipped_products.exists():
        return Response({'error': 'Order contains shipped items and cannot be cancelled'}, status=400)

    # Cancel the order
    order.tx_status = 'CANCELLED'
    order.save()

    # Cancel all ordered products
    order.orders_set.update(status='CANCELLED')



    return Response({
        'message': 'Order cancelled successfully',
        'order_id': str(order.id)
    })


# Helper functions
def _generate_tracking_timeline(order, ordered_products):
    """
    Generate tracking timeline based on order and product status

    Args:
        order: Order instance
        ordered_products: QuerySet of OrderedProduct instances

    Returns:
        list: List of tracking step dictionaries
    """
    from django.utils import timezone

    timeline = []
    now = timezone.now()

    # Order Placed
    timeline.append({
        'icon': 'check_circle',
        'title': 'Order Placed',
        'description': 'Your order has been placed successfully',
        'time': order.created_at.strftime("%d %b %Y, %I:%M %p"),
        'is_completed': True,
        'timestamp': order.created_at.isoformat()
    })

    # Order Confirmed
    confirmed_time = order.created_at + timezone.timedelta(minutes=30)
    timeline.append({
        'icon': 'inventory_2',
        'title': 'Order Confirmed',
        'description': 'Your order has been confirmed and is being prepared',
        'time': confirmed_time.strftime("%d %b %Y, %I:%M %p"),
        'is_completed': order.tx_status == 'SUCCESS',
        'timestamp': confirmed_time.isoformat()
    })

    # Check if any product is out for delivery
    out_for_delivery = ordered_products.filter(status='OUT_FOR_DELIVERY').exists()
    delivery_time = order.created_at + timezone.timedelta(days=1)

    timeline.append({
        'icon': 'local_shipping',
        'title': 'Out for Delivery',
        'description': 'Your order is on the way',
        'time': delivery_time.strftime("%d %b %Y, %I:%M %p"),
        'is_completed': out_for_delivery,
        'timestamp': delivery_time.isoformat()
    })

    # Delivered
    delivered = ordered_products.filter(status='DELIVERED').exists()
    expected_delivery = order.expected_delivery if hasattr(order,
                                                           'expected_delivery') and order.expected_delivery else _calculate_expected_delivery(
        order)

    timeline.append({
        'icon': 'home',
        'title': 'Delivered',
        'description': 'Your order has been delivered',
        'time': expected_delivery,
        'is_completed': delivered,
        'timestamp': (order.created_at + timezone.timedelta(days=2)).isoformat()
    })

    return timeline

def _get_delivery_partner_info(order):
    """Get delivery partner information (customize based on your system)"""
    # This is mock data - integrate with your actual delivery partner API
    partners = [
        {
            'name': 'Raj Kumar',
            'phone': '+91 98765 43210',
            'vehicle_number': 'MH12AB1234',
            'rating': 4.8
        },
        {
            'name': 'Priya Sharma',
            'phone': '+91 87654 32109',
            'vehicle_number': 'DL08CD5678',
            'rating': 4.9
        },
        {
            'name': 'Amit Singh',
            'phone': '+91 76543 21098',
            'vehicle_number': 'KA03EF9012',
            'rating': 4.7
        }
    ]

    # Return a partner based on order ID (for consistency)
    partner_index = int(str(order.id)[-1]) % len(partners)
    return partners[partner_index]


def _calculate_expected_delivery(order):
    """
    ✅ UPGRADED: Calculate expected delivery based on rental start date
    Shows the actual date user selected for rental, not a calculated future date

    Args:
        order: Order instance

    Returns:
        str: Formatted expected delivery date (e.g., "15 Dec 2025")
    """
    from django.utils import timezone

    # Get the first ordered product to check for rental dates
    first_item = order.orders_set.select_related('product_option__product').first()

    if first_item and first_item.rental_start_date:
        # For rentals, expected delivery is the rental start date
        return first_item.rental_start_date.strftime("%d %b %Y")

    # Check if order has stored expected_delivery
    if hasattr(order, 'expected_delivery') and order.expected_delivery:
        return order.expected_delivery

    # For purchases or items without rental dates, add 2 days
    expected = order.created_at + timezone.timedelta(days=2)
    return expected.strftime("%d %b %Y")


def _get_current_tracking_status(order):
    """
    Get current human-readable tracking status

    Args:
        order: Order instance

    Returns:
        str: Human-readable status (e.g., "Order Confirmed", "Delivered")
    """
    status_map = {
        'INITIATED': 'Order Initiated',
        'PENDING': 'Payment Pending',
        'SUCCESS': 'Order Confirmed',
        'CANCELLED': 'Order Cancelled',
        'FAILED': 'Payment Failed',
    }

    # Check ordered products status
    ordered_products = order.orders_set.all()

    if ordered_products.filter(status='DELIVERED').exists():
        return 'Delivered'
    elif ordered_products.filter(status='OUT_FOR_DELIVERY').exists():
        return 'Out for Delivery'
    elif ordered_products.filter(status='CANCELLED').exists():
        return 'Cancelled'
    else:
        return status_map.get(order.tx_status, 'Processing')

# Add these imports to your existing views.py imports
from backend.models import Slide
from backend.serializers import SlideSerializer


# Add this new slides endpoint function to your views.py
@api_view(['GET'])
@permission_classes([AllowAny])  # Public endpoint - no authentication required
def slides(request):
    """
    Get all promotional slides ordered by position
    """
    try:
        # Debug: Print slides count
        slides_count = Slide.objects.count()
        print(f"Total slides in database: {slides_count}")

        slides_list = Slide.objects.all().order_by('position')

        # Debug: Print each slide
        for slide in slides_list:
            print(f"Slide ID: {slide.id}, Position: {slide.position}, Image: {slide.image}")

        slides_data = []
        for slide in slides_list:
            try:
                # Build absolute URL for the image
                image_url = None
                if slide.image:
                    if request:
                        image_url = request.build_absolute_uri(slide.image.url)
                    else:
                        image_url = slide.image.url

                    print(f"Processing slide {slide.id}: Image URL = {image_url}")

                slide_data = {
                    'id': slide.id,
                    'position': slide.position,
                    'image': image_url,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id}: {e}")
                continue

        print(f"Returning {len(slides_data)} slides")
        return Response(slides_data, status=200)

    except Exception as e:
        print(f"Error fetching slides: {e}")
        return Response([], status=200)


# Enhanced home_screen_data function with better slides integration
# views.py - ✅ COMPLETE FIX for home endpoint


@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ This allows guests
def home_screen_data(request):
    """
    Enhanced home screen with home page items
    Query params: pincode (optional)
    ✅ NOW WORKS FOR GUESTS
    """
    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    if not is_authenticated:
        # ✅ Guest mode - create mock user for compatibility
        print("🎭 Guest mode detected - home screen")
        from django.contrib.auth.models import AnonymousUser
        user = AnonymousUser()
        user.email = 'guest@beautyhub.com'
        user.cart = type('Cart', (), {'count': lambda: 0})()
        user.wishlist = type('Wishlist', (), {'count': lambda: 0})()
    else:
        print(f"👤 Authenticated user: {user.email}")

    pincode = request.GET.get('pincode')

    print(f"\n{'=' * 60}")
    print(f"🏠 HOME SCREEN REQUEST")
    print(f"{'=' * 60}")
    print(f"📍 Pincode: {pincode}")
    print(f"👤 User: {user.email if hasattr(user, 'email') else 'Guest'}")
    print(f"🎭 Guest Mode: {not is_authenticated}")

    # Check serviceability
    is_serviceable = False
    location_info = None
    serviceability_message = "Please select your location"

    if pincode:
        from backend.utils import check_pincode_serviceability
        is_serviceable, location_info, serviceability_message = check_pincode_serviceability(pincode)
        print(f"✅ Serviceable: {is_serviceable}")
        if location_info:
            print(f"📍 Location: {location_info.area_name}, {location_info.city}")

    # If not serviceable, return minimal data
    if pincode and not is_serviceable:
        print(f"⚠️ Location not serviceable: {pincode}")
        print(f"{'=' * 60}\n")

        guest_user_data = {
            'email': 'guest@beautyhub.com',
            'phone': '',
            'fullname': 'Guest User',
            'notifications': 0,
            'wishlist': [],
            'cart': [],
        } if not is_authenticated else UserSerializer(user, many=False).data

        return Response({
            'is_serviceable': False,
            'message': serviceability_message,
            'pincode': pincode,
            'user': guest_user_data,
            'promotional_slides': [],
            'categories': [],
            'home_page_items': [],
            'recommended_products': [],
            'cart_count': 0,
            'wishlist_count': 0,
        })

    # Get user data
    if is_authenticated:
        user_data = UserSerializer(user, many=False).data
    else:
        user_data = {
            'email': 'guest@beautyhub.com',
            'phone': '',
            'fullname': 'Guest User',
            'notifications': 0,
            'wishlist': [],
            'cart': [],
        }

    # Get ALL categories
    print(f"\n📊 FETCHING CATEGORIES...")

    if location_info:
        from backend.utils import get_available_categories_for_pincode
        categories = get_available_categories_for_pincode(pincode)
    else:
        from backend.models import Category
        categories = Category.objects.all().order_by('position')

    print(f"✅ Found {categories.count()} categories")
    categories_data = CategorySerializer(categories, many=True, context={'request': request}).data

    # Get promotional slides
    try:
        slides = Slide.objects.all().order_by('position')
        slides_data = []
        for slide in slides:
            try:
                image_url = None
                if slide.image:
                    image_url = request.build_absolute_uri(slide.image.url) if request else slide.image.url
                slide_data = {
                    'id': slide.id,
                    'position': slide.position,
                    'image': image_url,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id}: {e}")
                continue
    except Exception as e:
        print(f"Error fetching slides: {e}")
        slides_data = []

    # Get Home Page Items
    try:
        if pincode:
            home_page_items = HomePageItem.objects.filter(
                is_active=True
            ).filter(
                Q(show_in_all_locations=True) |
                Q(specific_locations__pincode=pincode, specific_locations__is_active=True)
            ).distinct().order_by('item_type', 'position')
        else:
            home_page_items = HomePageItem.objects.filter(
                is_active=True,
                show_in_all_locations=True
            ).order_by('item_type', 'position')

        home_page_items_data = HomePageItemSerializer(
            home_page_items,
            many=True,
            context={'request': request}
        ).data
    except Exception as e:
        print(f"❌ Error loading home page items: {e}")
        home_page_items_data = []

    # Get recommended products
    recommended_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(options_set__quantity__gt=0).order_by('position', '-created_at')[:6]

    products_data = []
    for product in recommended_products:
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'position': getattr(product, 'position', 9999),
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    # Get counts
    cart_count = user.cart.count() if is_authenticated else 0
    wishlist_count = user.wishlist.count() if is_authenticated else 0

    # Male/Female home tile images (from admin: Home Male/Female tile images)
    from backend.models import HomeGenderTileImage
    tile_settings = HomeGenderTileImage.objects.first()
    male_tile_image_url = None
    female_tile_image_url = None
    if tile_settings:
        if tile_settings.male_tile_image:
            male_tile_image_url = request.build_absolute_uri(tile_settings.male_tile_image.url) if request else tile_settings.male_tile_image.url
        if tile_settings.female_tile_image:
            female_tile_image_url = request.build_absolute_uri(tile_settings.female_tile_image.url) if request else tile_settings.female_tile_image.url

    print(f"\n{'=' * 60}")
    print(f"📦 FINAL RESPONSE")
    print(f"✅ Categories: {len(categories_data)}")
    print(f"✅ Products: {len(products_data)}")
    print(f"🛒 Cart: {cart_count} | ❤️ Wishlist: {wishlist_count}")
    print(f"{'=' * 60}\n")

    return Response({
        'is_serviceable': True,
        'location_info': {
            'pincode': location_info.pincode,
            'area_name': location_info.area_name,
            'city': location_info.city,
            'state': location_info.state,
            'rent_available': location_info.rent_available,
            'service_available': location_info.service_available,
            'delivery_charge': location_info.delivery_charge,
            'delivery_time': location_info.delivery_time,
        } if location_info else None,
        'message': serviceability_message,
        'user': user_data,
        'categories': categories_data,
        'promotional_slides': slides_data,
        'home_page_items': home_page_items_data,
        'recommended_products': products_data,
        'cart_count': cart_count,
        'wishlist_count': wishlist_count,
        'unread_notifications': user_data.get('notifications', 0),
        'male_tile_image_url': male_tile_image_url,
        'female_tile_image_url': female_tile_image_url,
    })

# Add this to your views.py file

@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticatedUser])
def update_profile(request):
    """
    Update user profile information
    PUT: Complete profile update
    PATCH: Partial profile update
    """
    user = request.user

    try:
        # Get the data from request
        data = request.data

        # Validate email uniqueness if being updated
        new_email = data.get('email')
        if new_email and new_email != user.email:
            if User.objects.filter(email=new_email).exists():
                return Response({
                    'success': False,
                    'message': 'Email already exists'
                }, status=400)

        # Validate phone uniqueness if being updated
        new_phone = data.get('phone')
        if new_phone and new_phone != user.phone:
            if User.objects.filter(phone=new_phone).exists():
                return Response({
                    'success': False,
                    'message': 'Phone number already exists'
                }, status=400)

        # Update user fields
        if 'fullname' in data:
            user.fullname = data['fullname']
        if 'email' in data:
            user.email = data['email']
        if 'phone' in data:
            user.phone = data['phone']

        # Update address fields
        if 'name' in data:
            user.name = data['name']
        if 'address' in data:
            user.address = data['address']
        if 'pincode' in data:
            try:
                user.pincode = int(data['pincode']) if data['pincode'] else None
            except (ValueError, TypeError):
                pass
        if 'contact_no' in data:
            user.contact_no = data['contact_no']
        if 'district' in data:
            user.district = data['district']
        if 'state' in data:
            user.state = data['state']

        # Save the user
        user.save()

        # Return updated user data
        user_data = UserSerializer(user, many=False).data

        return Response({
            'success': True,
            'message': 'Profile updated successfully',
            'user': user_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to update profile: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def upload_profile_image(request):
    """
    Upload user profile image
    """
    user = request.user

    try:
        if 'profile_image' not in request.FILES:
            return Response({
                'success': False,
                'message': 'No image file provided'
            }, status=400)

        image_file = request.FILES['profile_image']

        # Validate file size (max 5MB)
        if image_file.size > 5 * 1024 * 1024:
            return Response({
                'success': False,
                'message': 'Image file too large (max 5MB)'
            }, status=400)

        # Validate file type
        allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
        if image_file.content_type not in allowed_types:
            return Response({
                'success': False,
                'message': 'Invalid image format. Only JPEG, PNG, and WebP are allowed'
            }, status=400)

        # TODO: Save image to your preferred storage (local, S3, etc.)
        # For now, we'll just return success
        # In production, you would:
        # 1. Save the image to storage
        # 2. Update user model with image URL/path
        # 3. Return the image URL

        return Response({
            'success': True,
            'message': 'Profile image uploaded successfully',
            'image_url': 'path/to/uploaded/image.jpg'  # Replace with actual URL
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to upload image: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_profile(request):
    """
    Get complete user profile information
    """
    user = request.user

    try:
        # Serialize user data with address information
        user_data = {
            'id': user.id,
            'fullname': user.fullname,
            'email': user.email,
            'phone': user.phone,
            'name': user.name or '',
            'address': user.address or '',
            'pincode': user.pincode,
            'contact_no': user.contact_no or '',
            'district': user.district or '',
            'state': user.state or '',
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'wishlist_count': user.wishlist.count(),
            'cart_count': user.cart.count(),
            'notifications': user.notifications_set.filter(seen=False).count(),
        }

        return Response({
            'success': True,
            'user': user_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to get profile: {str(e)}'
        }, status=500)


# Add these address management views to your views.py file

# views.py - Complete Address Management Endpoints with Queue Support

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_addresses(request):
    """
    Get all addresses for the authenticated user in queue order
    URL: /api/addresses/
    Returns: All addresses sorted by default first, then by creation date (newest first)
    """
    user = request.user

    try:
        print(f"Ã°Å¸â€œÂ Fetching addresses for user: {user.email}")

        # Get all addresses for this user with proper ordering
        # Default address first, then by creation date (newest first)
        user_addresses = UserAddress.objects.filter(user=user).order_by(
            '-is_default',  # Default address first (True before False)
            '-created_at'  # Then newest first
        )

        print(f"Ã¢Å“â€¦ Found {user_addresses.count()} addresses")

        addresses = []
        for idx, addr in enumerate(user_addresses):
            address_data = {
                'id': addr.id,
                'type': addr.type,
                'name': addr.name,
                'address': addr.address,
                'contact_no': addr.contact_no,
                'pincode': addr.pincode,
                'district': addr.district or '',
                'state': addr.state or '',
                'is_default': addr.is_default,
                'created_at': addr.created_at.isoformat() if addr.created_at else None
            }
            addresses.append(address_data)

            # Debug print to show queue order
            default_marker = "Ã¢Â­Â" if addr.is_default else "  "
            print(f"{default_marker} Position {idx + 1}: {addr.name} ({addr.type})")

        # Get the default address
        default_address = next((addr for addr in addresses if addr['is_default']), None)

        return Response({
            'success': True,
            'addresses': addresses,
            'total_count': len(addresses),
            'default_address': default_address,
            'message': 'Addresses fetched successfully'
        }, status=200)

    except Exception as e:
        print(f"Ã¢ÂÅ’ Error fetching addresses: {str(e)}")
        import traceback
        traceback.print_exc()

        return Response({
            'success': False,
            'message': f'Failed to fetch addresses: {str(e)}',
            'addresses': []
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_address(request):
    """
    Add a new address and return all addresses in queue order
    URL: /api/addresses/add/
    Body: {
        "type": "Home/Work/Other",
        "name": "John Doe",
        "address": "123 Street",
        "contact_no": "+91 9876543210",
        "pincode": 462011,
        "district": "Bhopal",
        "state": "Madhya Pradesh"
    }
    Returns: Newly created address + all addresses in queue order
    """
    user = request.user

    try:
        data = request.data
        print(f"Ã°Å¸â€œÂ Adding new address for user: {user.email}")
        print(f"Ã°Å¸â€œÂ Address data: {data}")

        # Validate required fields
        required_fields = ['name', 'address', 'contact_no']
        for field in required_fields:
            if not data.get(field):
                return Response({
                    'success': False,
                    'message': f'{field} is required'
                }, status=400)

        # Validate phone number
        contact_no = data.get('contact_no', '')
        if len(contact_no.replace('+91 ', '').replace(' ', '')) < 10:
            return Response({
                'success': False,
                'message': 'Contact number must be at least 10 digits'
            }, status=400)

        # Validate pincode if provided
        pincode = data.get('pincode')
        if pincode:
            try:
                pincode_int = int(pincode)
                if len(str(pincode_int)) != 6:
                    return Response({
                        'success': False,
                        'message': 'Pincode must be 6 digits'
                    }, status=400)
            except (ValueError, TypeError):
                return Response({
                    'success': False,
                    'message': 'Invalid pincode format'
                }, status=400)
            # Validate pincode is in serviceable locations
            from backend.utils import check_pincode_serviceability
            is_serviceable, _, serviceability_message = check_pincode_serviceability(pincode_int)
            if not is_serviceable:
                return Response({
                    'success': False,
                    'message': 'We are not providing our services in your location yet. Coming soon!',
                    'is_location_not_serviceable': True
                }, status=400)

        # Check if this is the first address for the user
        existing_addresses_count = UserAddress.objects.filter(user=user).count()
        is_first_address = existing_addresses_count == 0

        print(f"Ã°Å¸â€œÅ  Existing addresses: {existing_addresses_count}")
        print(f"Ã°Å¸â€ â€¢ Is first address: {is_first_address}")

        # Create new address
        new_address = UserAddress.objects.create(
            user=user,
            type=data.get('type', 'Home'),
            name=data.get('name'),
            address=data.get('address'),
            contact_no=data.get('contact_no'),
            pincode=int(pincode) if pincode else None,
            district=data.get('district', ''),
            state=data.get('state', ''),
            is_default=is_first_address  # First address is automatically default
        )

        print(f"Ã¢Å“â€¦ Address created with ID: {new_address.id}")
        print(f"Ã¢Â­Â Is default: {new_address.is_default}")

        # Get all addresses in queue order (default first, then newest)
        all_addresses = UserAddress.objects.filter(user=user).order_by(
            '-is_default',
            '-created_at'
        )

        print(f"Ã°Å¸â€œâ€¹ Address Queue Order:")
        addresses_list = []
        for idx, addr in enumerate(all_addresses):
            address_dict = {
                'id': addr.id,
                'type': addr.type,
                'name': addr.name,
                'address': addr.address,
                'contact_no': addr.contact_no,
                'pincode': addr.pincode,
                'district': addr.district or '',
                'state': addr.state or '',
                'is_default': addr.is_default,
                'created_at': addr.created_at.isoformat(),
            }
            addresses_list.append(address_dict)

            # Debug print
            default_marker = "Ã¢Â­Â" if addr.is_default else "  "
            new_marker = "Ã°Å¸â€ â€¢" if addr.id == new_address.id else ""
            print(f"{default_marker} {new_marker} Position {idx + 1}: {addr.name} ({addr.type})")

        return Response({
            'success': True,
            'message': 'Address added successfully',
            'address': {
                'id': new_address.id,
                'type': new_address.type,
                'name': new_address.name,
                'address': new_address.address,
                'contact_no': new_address.contact_no,
                'pincode': new_address.pincode,
                'district': new_address.district,
                'state': new_address.state,
                'is_default': new_address.is_default,
                'created_at': new_address.created_at.isoformat(),
            },
            'addresses': addresses_list,  # All addresses in queue order
            'total_count': len(addresses_list)
        }, status=201)

    except Exception as e:
        print(f"Ã¢ÂÅ’ Error adding address: {str(e)}")
        import traceback
        traceback.print_exc()

        return Response({
            'success': False,
            'message': f'Failed to add address: {str(e)}'
        }, status=500)


@api_view(['PUT'])
@permission_classes([IsAuthenticatedUser])
def update_address(request, address_id):
    """
    Update an existing address and return all addresses in queue order
    URL: /api/addresses/<address_id>/update/
    Returns: Updated address + all addresses in queue order
    """
    user = request.user

    try:
        print(f"Ã°Å¸â€œÂ Updating address {address_id} for user: {user.email}")

        # Get address from UserAddress model
        address = UserAddress.objects.get(id=address_id, user=user)
        print(f"Ã¢Å“â€¦ Found address: {address.name}")

        data = request.data
        print(f"Ã°Å¸â€œÂ Update data: {data}")

        # Validate required fields
        required_fields = ['name', 'address', 'contact_no']
        for field in required_fields:
            if not data.get(field):
                return Response({
                    'success': False,
                    'message': f'{field} is required'
                }, status=400)

        # Validate phone number
        contact_no = data.get('contact_no', '')
        if len(contact_no.replace('+91 ', '').replace(' ', '')) < 10:
            return Response({
                'success': False,
                'message': 'Contact number must be at least 10 digits'
            }, status=400)

        # Validate pincode if provided
        pincode = data.get('pincode')
        if pincode:
            try:
                pincode_int = int(pincode)
                if len(str(pincode_int)) != 6:
                    return Response({
                        'success': False,
                        'message': 'Pincode must be 6 digits'
                    }, status=400)
            except (ValueError, TypeError):
                return Response({
                    'success': False,
                    'message': 'Invalid pincode format'
                }, status=400)
            # Validate pincode is in serviceable locations
            from backend.utils import check_pincode_serviceability
            is_serviceable, _, _ = check_pincode_serviceability(pincode_int)
            if not is_serviceable:
                return Response({
                    'success': False,
                    'message': 'We are not providing our services in your location yet. Coming soon!',
                    'is_location_not_serviceable': True
                }, status=400)

        # Update fields
        address.type = data.get('type', address.type)
        address.name = data.get('name')
        address.address = data.get('address')
        address.contact_no = data.get('contact_no')
        address.pincode = int(pincode) if pincode else None
        address.district = data.get('district', '')
        address.state = data.get('state', '')
        address.save()

        print(f"Ã¢Å“â€¦ Address updated successfully")

        # Get all addresses in queue order
        all_addresses = UserAddress.objects.filter(user=user).order_by(
            '-is_default',
            '-created_at'
        )

        print(f"Ã°Å¸â€œâ€¹ Address Queue Order:")
        addresses_list = []
        for idx, addr in enumerate(all_addresses):
            address_dict = {
                'id': addr.id,
                'type': addr.type,
                'name': addr.name,
                'address': addr.address,
                'contact_no': addr.contact_no,
                'pincode': addr.pincode,
                'district': addr.district or '',
                'state': addr.state or '',
                'is_default': addr.is_default,
                'created_at': addr.created_at.isoformat(),
            }
            addresses_list.append(address_dict)

            # Debug print
            default_marker = "Ã¢Â­Â" if addr.is_default else "  "
            updated_marker = "Ã°Å¸â€œÂ" if addr.id == address.id else ""
            print(f"{default_marker} {updated_marker} Position {idx + 1}: {addr.name} ({addr.type})")

        return Response({
            'success': True,
            'message': 'Address updated successfully',
            'address': {
                'id': address.id,
                'type': address.type,
                'name': address.name,
                'address': address.address,
                'contact_no': address.contact_no,
                'pincode': address.pincode,
                'district': address.district,
                'state': address.state,
                'is_default': address.is_default,
                'created_at': address.created_at.isoformat(),
            },
            'addresses': addresses_list,  # All addresses in queue order
            'total_count': len(addresses_list)
        }, status=200)

    except UserAddress.DoesNotExist:
        print(f"Ã¢ÂÅ’ Address {address_id} not found")
        return Response({
            'success': False,
            'message': 'Address not found'
        }, status=404)
    except Exception as e:
        print(f"Ã¢ÂÅ’ Error updating address: {str(e)}")
        import traceback
        traceback.print_exc()

        return Response({
            'success': False,
            'message': f'Failed to update address: {str(e)}'
        }, status=500)


def _get_product_reviews(product, page=1, page_size=5):
    """
    ✅ Get paginated reviews for a product with full review text
    Returns reviews with user info, rating, text, and helpful count

    Args:
        product: Product instance
        page: Page number (default: 1)
        page_size: Reviews per page (default: 5)

    Returns:
        dict: Review data with pagination info
    """
    from django.core.paginator import Paginator

    # Get all reviews for this product (from OrderedProduct ratings)
    reviews_queryset = OrderedProduct.objects.filter(
        product_option__product=product,
        rating__isnull=False,
        rating__gt=0
    ).select_related('order__user').order_by('-created_at')

    # Pagination
    paginator = Paginator(reviews_queryset, page_size)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    reviews = []
    for ordered_product in page_obj:
        # Get review text - check if review_text field exists, otherwise use empty
        review_text = ''
        if hasattr(ordered_product, 'review_text'):
            review_text = ordered_product.review_text or ''

        # Get user name safely
        user_name = 'Anonymous'
        user_initial = 'A'
        if ordered_product.order and ordered_product.order.user:
            if ordered_product.order.user.fullname:
                user_name = ordered_product.order.user.fullname
                user_initial = user_name[0].upper()

        # Build review data
        review = {
            'id': str(ordered_product.id),
            'user_name': user_name,
            'user_initial': user_initial,
            'rating': ordered_product.rating,
            'review_text': review_text,
            'review_date': ordered_product.created_at.strftime('%B %d, %Y'),
            'verified_purchase': True,  # All OrderedProduct reviews are verified
            'helpful_count': getattr(ordered_product, 'helpful_count', 0),
            'product_option': ordered_product.product_option.option or 'Standard',
        }
        reviews.append(review)

    return {
        'reviews': reviews,
        'total_reviews': paginator.count,
        'current_page': page_obj.number,
        'total_pages': paginator.num_pages,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
    }

@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def delete_address(request, address_id):
    """
    Delete an address and return remaining addresses in queue order
    URL: /api/addresses/<address_id>/delete/
    Returns: Remaining addresses in queue order
    """
    user = request.user

    try:
        print(f"Ã°Å¸â€œÂ Deleting address {address_id} for user: {user.email}")

        # Get address from UserAddress model
        address = UserAddress.objects.get(id=address_id, user=user)
        address_name = address.name
        was_default = address.is_default

        print(f"Ã¢Å“â€¦ Found address: {address_name}")
        print(f"Ã¢Â­Â Was default: {was_default}")

        # If deleting default address, set another as default
        if was_default:
            next_address = UserAddress.objects.filter(user=user).exclude(id=address_id).first()
            if next_address:
                next_address.is_default = True
                next_address.save()
                print(f"Ã¢Â­Â New default set to: {next_address.name}")

        # Delete the address
        address.delete()
        print(f"Ã°Å¸â€”â€˜Ã¯Â¸Â Address deleted successfully")

        # Get remaining addresses in queue order
        all_addresses = UserAddress.objects.filter(user=user).order_by(
            '-is_default',
            '-created_at'
        )

        print(f"Ã°Å¸â€œâ€¹ Remaining Address Queue Order:")
        addresses_list = []
        for idx, addr in enumerate(all_addresses):
            address_dict = {
                'id': addr.id,
                'type': addr.type,
                'name': addr.name,
                'address': addr.address,
                'contact_no': addr.contact_no,
                'pincode': addr.pincode,
                'district': addr.district or '',
                'state': addr.state or '',
                'is_default': addr.is_default,
                'created_at': addr.created_at.isoformat(),
            }
            addresses_list.append(address_dict)

            # Debug print
            default_marker = "Ã¢Â­Â" if addr.is_default else "  "
            print(f"{default_marker} Position {idx + 1}: {addr.name} ({addr.type})")

        return Response({
            'success': True,
            'message': f'Address "{address_name}" deleted successfully',
            'addresses': addresses_list,  # Remaining addresses in queue order
            'total_count': len(addresses_list),
            'new_default': addresses_list[0] if addresses_list else None
        }, status=200)

    except UserAddress.DoesNotExist:
        print(f"Ã¢ÂÅ’ Address {address_id} not found")
        return Response({
            'success': False,
            'message': 'Address not found'
        }, status=404)
    except Exception as e:
        print(f"Ã¢ÂÅ’ Error deleting address: {str(e)}")
        import traceback
        traceback.print_exc()

        return Response({
            'success': False,
            'message': f'Failed to delete address: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def set_default_address(request, address_id):
    """
    Set an address as default and return all addresses in queue order
    URL: /api/addresses/<address_id>/set-default/
    Returns: All addresses with updated default status in queue order
    """
    user = request.user

    try:
        print(f"Ã°Å¸â€œÂ Setting address {address_id} as default for user: {user.email}")

        # Remove default from all addresses
        UserAddress.objects.filter(user=user).update(is_default=False)
        print(f"Ã¢Å“â€¦ Removed default from all addresses")

        # Set this address as default
        address = UserAddress.objects.get(id=address_id, user=user)
        address.is_default = True
        address.save()

        print(f"Ã¢Â­Â Set {address.name} as default")

        # Get all addresses in queue order (default first)
        all_addresses = UserAddress.objects.filter(user=user).order_by(
            '-is_default',
            '-created_at'
        )

        print(f"Ã°Å¸â€œâ€¹ Updated Address Queue Order:")
        addresses_list = []
        for idx, addr in enumerate(all_addresses):
            address_dict = {
                'id': addr.id,
                'type': addr.type,
                'name': addr.name,
                'address': addr.address,
                'contact_no': addr.contact_no,
                'pincode': addr.pincode,
                'district': addr.district or '',
                'state': addr.state or '',
                'is_default': addr.is_default,
                'created_at': addr.created_at.isoformat(),
            }
            addresses_list.append(address_dict)

            # Debug print
            default_marker = "Ã¢Â­Â" if addr.is_default else "  "
            new_default_marker = "Ã°Å¸â€ â€¢" if addr.id == address.id else ""
            print(f"{default_marker} {new_default_marker} Position {idx + 1}: {addr.name} ({addr.type})")

        return Response({
            'success': True,
            'message': f'{address.name} set as default address',
            'addresses': addresses_list,  # All addresses in new queue order
            'total_count': len(addresses_list),
            'default_address': addresses_list[0] if addresses_list else None
        }, status=200)

    except UserAddress.DoesNotExist:
        print(f"Ã¢ÂÅ’ Address {address_id} not found")
        return Response({
            'success': False,
            'message': 'Address not found'
        }, status=404)
    except Exception as e:
        print(f"Ã¢ÂÅ’ Error setting default address: {str(e)}")
        import traceback
        traceback.print_exc()

        return Response({
            'success': False,
            'message': f'Failed to set default address: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def create_order(request):
    """
    Create a new order with ordered products
    Body: {
        "cart_items": [{"product_option_id": "uuid", "quantity": 1}],
        "payment_mode": "COD" or "ONLINE",
        "address": "delivery address",
        "tx_id": "transaction_id" (optional for online payment),
        "tx_status": "SUCCESS" (optional, defaults to INITIATED)
    }
    """
    from django.utils import timezone

    user = request.user
    data = request.data

    # Validate required fields
    cart_items = data.get('cart_items', [])
    payment_mode = data.get('payment_mode')
    address = data.get('address')

    if not cart_items:
        return Response({
            'success': False,
            'message': 'Cart items are required'
        }, status=400)

    if not payment_mode or not address:
        return Response({
            'success': False,
            'message': 'Payment mode and address are required'
        }, status=400)

    try:
        with transaction.atomic():
            # Calculate total amount
            total_amount = 0
            items_to_order = []

            for item in cart_items:
                product_option_id = item.get('product_option_id')
                quantity = item.get('quantity', 1)

                try:
                    product_option = ProductOption.objects.select_related('product').get(
                        id=product_option_id
                    )
                except ProductOption.DoesNotExist:
                    return Response({
                        'success': False,
                        'message': f'Product option {product_option_id} not found'
                    }, status=404)

                # Check stock
                if product_option.quantity < quantity:
                    return Response({
                        'success': False,
                        'message': f'Insufficient stock for {product_option.product.title}'
                    }, status=400)

                # Calculate prices
                product_price = product_option.product.price
                offer_price = product_option.product.offer_price
                effective_price = offer_price if offer_price > 0 else product_price
                delivery_charge = product_option.product.delivery_charge

                item_total = (effective_price * quantity) + delivery_charge
                total_amount += item_total

                items_to_order.append({
                    'product_option': product_option,
                    'quantity': quantity,
                    'product_price': product_price,
                    'tx_price': effective_price,
                    'delivery_price': delivery_charge,
                })

            # Create Order (initial total before wallet usage)
            order = Order.objects.create(
                user=user,
                tx_amount=total_amount,
                payment_mode=payment_mode,
                address=address,
                tx_id=data.get('tx_id', ''),
                tx_status=data.get('tx_status', 'INITIATED'),
                tx_time=timezone.now().strftime("%d %b %Y %H:%M %p"),
                tx_msg=data.get('tx_msg', ''),
                from_cart=data.get('from_cart', True),
                latitude=data.get('latitude'),
                longitude=data.get('longitude'),
            )

            # Create OrderedProduct entries
            ordered_products = []
            for item in items_to_order:
                ordered_product = OrderedProduct.objects.create(
                    order=order,
                    product_option=item['product_option'],
                    quantity=item['quantity'],
                    product_price=item['product_price'],
                    tx_price=item['tx_price'],
                    delivery_price=item['delivery_price'],
                    status='ORDERED'
                )
                ordered_products.append(ordered_product)

                # Reduce stock
                item['product_option'].quantity -= item['quantity']
                item['product_option'].save()

            # Clear cart if from_cart is True
            if data.get('from_cart', True):
                user.cart.clear()

            # Prepare response
            order_data = {
                'id': str(order.id),
                'order_number': f"BH{str(order.id)[:8].upper()}",
                'total_amount': order.tx_amount,
                'payment_mode': order.payment_mode,
                'status': order.tx_status,
                'created_at': order.created_at.isoformat(),
                'items_count': len(ordered_products)
            }

            return Response({
                'success': True,
                'message': 'Order placed successfully',
                'order': order_data
            }, status=201)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to create order: {str(e)}'
        }, status=500)

# Also add the profile edit endpoint for your repository
@api_view(['PUT'])
@permission_classes([IsAuthenticatedUser])
def edit_profile(request):
    """
    Edit user profile information
    URL: /api/profile/edit/
    """
    user = request.user

    try:
        data = request.data

        # Validate email uniqueness if being updated
        new_email = data.get('email')
        if new_email and new_email != user.email:
            if User.objects.filter(email=new_email).exists():
                return Response({
                    'success': False,
                    'message': 'Email already exists'
                }, status=400)

        # Validate phone uniqueness if being updated
        new_phone = data.get('phone')
        if new_phone and new_phone != user.phone:
            if User.objects.filter(phone=new_phone).exists():
                return Response({
                    'success': False,
                    'message': 'Phone number already exists'
                }, status=400)

        # Update profile fields
        if 'fullname' in data:
            user.fullname = data['fullname']
        if 'email' in data:
            user.email = data['email']
        if 'phone' in data:
            user.phone = data['phone']

        # Update address fields if provided
        if 'name' in data:
            user.name = data['name']
        if 'address' in data:
            user.address = data['address']
        if 'contact_no' in data:
            user.contact_no = data['contact_no']
        if 'pincode' in data:
            try:
                user.pincode = int(data['pincode']) if data['pincode'] else None
            except (ValueError, TypeError):
                pass
        if 'district' in data:
            user.district = data['district']
        if 'state' in data:
            user.state = data['state']

        user.save()

        # Return updated user data
        user_data = {
            'id': user.id,
            'fullname': user.fullname,
            'email': user.email,
            'phone': user.phone,
            'name': user.name or '',
            'address': user.address or '',
            'contact_no': user.contact_no or '',
            'pincode': user.pincode,
            'district': user.district or '',
            'state': user.state or '',
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'wishlist_count': user.wishlist.count(),
            'cart_count': user.cart.count(),
            'notifications': user.notifications_set.filter(seen=False).count(),
        }

        return Response({
            'success': True,
            'message': 'Profile updated successfully',
            'user': user_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to update profile: {str(e)}'
        }, status=500)

#todo wishlist enhanced here



@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_wishlist_enhanced(request):
    """
    Enhanced add to wishlist with proper validation
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({
            'success': False,
            'message': 'Product option ID is required'
        }, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

    # Check if already in wishlist
    if user.wishlist.filter(id=product_option_id).exists():
        return Response({
            'success': False,
            'message': 'Item already in wishlist'
        }, status=200)

    # Add to wishlist
    user.wishlist.add(product_option)

    return Response({
        'success': True,
        'message': 'Item added to wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_wishlist_enhanced(request, product_option_id):
    """
    Enhanced remove from wishlist
    """
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

    if not user.wishlist.filter(id=product_option_id).exists():
        return Response({
            'success': False,
            'message': 'Item not in wishlist'
        }, status=400)

    # Store item data for undo functionality
    removed_item = {
        'id': str(product_option.id),
        'name': str(product_option),
    }

    user.wishlist.remove(product_option)

    return Response({
        'success': True,
        'message': 'Item removed from wishlist',
        'wishlist_count': user.wishlist.count(),
        'removed_item': removed_item
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def move_wishlist_to_cart(request):
    """
    Move all available wishlist items to cart
    """
    user = request.user
    wishlist_items = user.wishlist.filter(quantity__gt=0).all()

    if not wishlist_items:
        return Response({
            'success': False,
            'message': 'No available items in wishlist to move to cart'
        }, status=400)

    moved_items = []
    already_in_cart = []

    with transaction.atomic():
        for item in wishlist_items:
            if not user.cart.filter(id=item.id).exists():
                user.cart.add(item)
                user.wishlist.remove(item)
                moved_items.append(str(item))
            else:
                user.wishlist.remove(item)
                already_in_cart.append(str(item))

    return Response({
        'success': True,
        'message': f'{len(moved_items)} items moved to cart successfully',
        'moved_items_count': len(moved_items),
        'already_in_cart_count': len(already_in_cart),
        'cart_count': user.cart.count(),
        'wishlist_count': user.wishlist.count()
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def share_wishlist(request):
    """
    Generate shareable wishlist link or data
    """
    user = request.user
    wishlist_items = user.wishlist.select_related('product').all()

    if not wishlist_items:
        return Response({
            'success': False,
            'message': 'Wishlist is empty'
        }, status=400)

    # Create shareable data
    share_data = {
        'user_name': user.fullname or user.email,
        'total_items': len(wishlist_items),
        'items': []
    }

    for item in wishlist_items:
        share_data['items'].append({
            'name': str(item),
            'price': item.product.offer_price if item.product.offer_price > 0 else item.product.price,
            'category': item.product.category.name if item.product.category else 'Other'
        })

    # In a real implementation, you might:
    # 1. Create a temporary shareable link with expiration
    # 2. Generate a shareable image
    # 3. Create deep links to your app

    return Response({
        'success': True,
        'message': 'Wishlist prepared for sharing',
        'share_data': share_data,
        'share_text': f"Check out {user.fullname or 'my'} wishlist with {len(wishlist_items)} amazing items!",
        'share_url': f"https://yourapp.com/shared-wishlist/{user.id}"  # Replace with actual URL
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def undo_wishlist_removal(request):
    """
    Undo the last wishlist item removal
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({
            'success': False,
            'message': 'Product option ID is required'
        }, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

    # Add back to wishlist
    if not user.wishlist.filter(id=product_option_id).exists():
        user.wishlist.add(product_option)

    return Response({
        'success': True,
        'message': 'Item restored to wishlist',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def clear_wishlist(request):
    """
    Clear all items from wishlist
    """
    user = request.user
    items_count = user.wishlist.count()

    if items_count == 0:
        return Response({
            'success': False,
            'message': 'Wishlist is already empty'
        }, status=400)

    user.wishlist.clear()

    return Response({
        'success': True,
        'message': f'All {items_count} items removed from wishlist',
        'wishlist_count': 0
    })














@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def create_service_booking(request):
    """
    Create a new service booking

    URL: /api/services/bookings/create/
    """
    from django.utils import timezone
    from datetime import datetime as dt

    user = request.user

    # Get booking data
    service_option_id = request.data.get('service_option')
    booking_date_str = request.data.get('booking_date')
    booking_time_str = request.data.get('booking_time')
    customer_name = request.data.get('customer_name')
    customer_phone = request.data.get('customer_phone')
    customer_address = request.data.get('customer_address')
    notes = request.data.get('notes', '')
    coupon_code = (request.data.get('coupon_code') or '').strip().upper()

    # Validate required fields
    if not all([service_option_id, booking_date_str, booking_time_str,
                customer_name, customer_phone, customer_address]):
        return Response({
            'success': False,
            'message': 'Missing required fields'
        }, status=400)

    try:
        # Get the service option
        service_option = ServiceOption.objects.select_related('service').get(
            id=service_option_id
        )
    except ServiceOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Service option not found'
        }, status=404)

    # Parse and validate date
    try:
        booking_date = dt.strptime(booking_date_str, '%Y-%m-%d').date()
    except ValueError:
        return Response({
            'success': False,
            'message': 'Invalid date format. Use YYYY-MM-DD'
        }, status=400)

    # Check if date is in the future
    if booking_date < timezone.now().date():
        return Response({
            'success': False,
            'message': 'Booking date must be in the future'
        }, status=400)

    # Artist-specific: block booking if date is blocked or admin-booked
    service = service_option.service
    artist_date = ArtistAvailability.objects.filter(
        artist=service,
        date=booking_date,
        status__in=[ArtistAvailability.STATUS_BLOCKED, ArtistAvailability.STATUS_BOOKED],
    ).first()
    if artist_date:
        return Response({
            'success': False,
            'message': 'Date already unavailable'
        }, status=400)

    # Parse and validate time
    try:
        booking_time = dt.strptime(booking_time_str, '%H:%M:%S').time()
    except ValueError:
        return Response({
            'success': False,
            'message': 'Invalid time format. Use HH:MM:SS'
        }, status=400)

    # Check if the slot is already booked
    existing_booking = ServiceBooking.objects.filter(
        service_option=service_option,
        booking_date=booking_date,
        booking_time=booking_time,
        status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
    ).exists()

    if existing_booking:
        return Response({
            'success': False,
            'message': 'This time slot is already booked'
        }, status=400)

    # Apply coupon if provided (validate and get discounted amount)
    total_amount = service_option.price
    coupon_obj = None
    if coupon_code:
        from backend.utils import validate_coupon_and_calculate_discount
        success, msg, discount_amount, final_total, coupon_obj = validate_coupon_and_calculate_discount(
            coupon_code=coupon_code,
            user=user,
            cart_total=service_option.price,
            service_ids=[str(service.id)],
        )
        if not success:
            return Response({
                'success': False,
                'message': msg or 'Invalid or expired coupon code',
            }, status=400)
        total_amount = final_total

    try:
        # Create the booking
        booking = ServiceBooking.objects.create(
            user=user,
            service_option=service_option,
            booking_date=booking_date,
            booking_time=booking_time,
            duration=service_option.duration if hasattr(service_option, 'duration') else '1 hour',
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_address=customer_address,
            total_amount=total_amount,
            notes=notes,
            status='PENDING',
            payment_status='PENDING'
        )

        # Record coupon usage and increment used_count (so limit counts both orders and service bookings)
        if coupon_obj:
            from backend.models import CouponUsage
            CouponUsage.objects.create(
                user=user,
                coupon=coupon_obj,
                order=None,
                service_booking=booking,
            )
            coupon_obj.used_count += 1
            coupon_obj.save(update_fields=['used_count', 'updated_at'])

        # Apply referral wallet (after coupon)
        settings_obj = ReferralSettings.get_active()
        max_wallet_percent = settings_obj.max_wallet_usage_percent if settings_obj else 20
        requested_wallet_amount = int(request.data.get('wallet_amount') or 0)
        wallet_balance = int(user.referral_wallet_balance or 0)
        max_wallet_from_percent = int(total_amount * max_wallet_percent / 100) if max_wallet_percent > 0 else 0
        wallet_to_use = max(0, min(wallet_balance, requested_wallet_amount, max_wallet_from_percent, int(total_amount)))

        if wallet_to_use > 0:
            # Deduct from user wallet
            new_balance = wallet_balance - wallet_to_use
            user.referral_wallet_balance = new_balance
            user.save(update_fields=['referral_wallet_balance'])

            # Update booking amount
            booking.total_amount = int(total_amount) - wallet_to_use
            booking.save(update_fields=['total_amount'])

            # Ledger entry
            WalletTransaction.objects.create(
                user=user,
                amount=wallet_to_use,
                type=WalletTransaction.TYPE_DEBIT,
                description="Used in service booking",
                service_booking=booking,
            )

        # Serialize the created booking
        booking_data = {
            'id': str(booking.id),
            'service_name': service_option.service.title,
            'option_name': service_option.option_name,
            'booking_date': booking.booking_date.strftime('%Y-%m-%d'),
            'booking_time': booking.booking_time.strftime('%H:%M:%S'),
            'customer_name': booking.customer_name,
            'customer_phone': booking.customer_phone,
            'customer_address': booking.customer_address,
            'total_amount': booking.total_amount,
            'wallet_used': wallet_to_use if 'wallet_to_use' in locals() else 0,
            'status': booking.status,
            'payment_status': booking.payment_status,
            'created_at': booking.created_at.isoformat() if hasattr(booking,
                                                                    'created_at') else timezone.now().isoformat(),
        }

        # Send notification to user (wrapped in try-catch to not break if notification fails)
        try:
            pass
        except Exception as e:
            print(f"Failed to send notification: {e}")

        return Response({
            'success': True,
            'message': 'Booking created successfully',
            'booking': booking_data
        }, status=201)

    except Exception as e:
        print(f"Error creating booking: {e}")
        return Response({
            'success': False,
            'message': f'Failed to create booking: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_user_service_bookings(request):
    user = request.user
    status_filter = request.GET.get('status', None)

    bookings = ServiceBooking.objects.filter(user=user).order_by('-created_at')

    if status_filter:
        bookings = bookings.filter(status=status_filter)

    # IMPORTANT: Pass request context
    bookings_data = ServiceBookingSerializer(
        bookings,
        many=True,
        context={'request': request}  # ADD THIS
    ).data

    return Response({
        'bookings': bookings_data,
        'total_bookings': len(bookings_data)
    })


def _get_service_reviews(service):
    """
    Get reviews for a service
    This is a placeholder - implement actual review system
    """
    # Mock reviews for now
    mock_reviews = [
        {
            'user_name': 'Happy Customer',
            'rating': 5,
            'review_text': 'Amazing work, loved the service!',
            'created_at': datetime.datetime.now().isoformat()  # Changed to datetime.datetime.now()
        },
        {
            'user_name': 'Satisfied Client',
            'rating': 5,
            'review_text': 'Very professional and detailed work.',
            'created_at': datetime.datetime.now().isoformat()  # Changed to datetime.datetime.now()
        }
    ]

    return mock_reviews[:2]  # Return top 2 reviews


@api_view(['GET'])
@permission_classes([AllowAny])  # Allow guests to view availability (required for Select Date & Time)
def get_service_availability(request, service_id):
    """
    Get service availability for a specific month

    URL: /api/services/<service_id>/availability/
    Query Params: year, month
    """
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Service not found'
        }, status=404)

    # Get year and month from query params - FIX HERE
    from django.utils import timezone
    now = timezone.now()
    year = int(request.GET.get('year', now.year))
    month = int(request.GET.get('month', now.month))

    # Validate month and year
    if not (1 <= month <= 12):
        return Response({
            'success': False,
            'message': 'Invalid month'
        }, status=400)

    # Get all bookings for this service (artist) in the specified month
    service_options = service.options_set.all()
    bookings = ServiceBooking.objects.filter(
        service_option__in=service_options,
        booking_date__year=year,
        booking_date__month=month,
        status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
    )
    booked_dates = list(bookings.values_list('booking_date__day', flat=True).distinct())

    # Artist-specific availability: blocked and admin-booked dates for THIS artist only
    artist_avail = ArtistAvailability.objects.filter(
        artist=service,
        date__year=year,
        date__month=month,
    )
    blocked_dates = list(artist_avail.filter(status=ArtistAvailability.STATUS_BLOCKED).values_list('date__day', flat=True).distinct())
    artist_booked_dates = list(artist_avail.filter(status=ArtistAvailability.STATUS_BOOKED).values_list('date__day', flat=True).distinct())

    # Combined: customer bookings (red) + admin "booked" (red); blocked (grey) returned separately
    all_unavailable = set(booked_dates) | set(artist_booked_dates) | set(blocked_dates)

    total_days = monthrange(year, month)[1]
    today = timezone.now().date()

    available_dates = []
    for day in range(1, total_days + 1):
        from datetime import datetime as dt
        check_date = dt(year, month, day).date()
        if check_date >= today and day not in all_unavailable:
            available_dates.append(day)

    return Response({
        'success': True,
        'year': year,
        'month': month,
        'booked_dates': list(set(booked_dates) | set(artist_booked_dates)),  # red: customer + admin booked
        'blocked_dates': blocked_dates,  # grey: admin blocked
        'available_dates': available_dates,
        'total_days': total_days
    })


@api_view(['GET'])
@permission_classes([AllowAny])  # Allow guests to view time slots (required for Select Date & Time)
def get_available_time_slots(request, service_id):
    """
    Get available time slots for a specific date

    URL: /api/services/<service_id>/time-slots/
    Query Params: date (YYYY-MM-DD)
    """
    from django.utils import timezone
    from datetime import datetime as dt

    date_str = request.GET.get('date')

    if not date_str:
        return Response({
            'success': False,
            'message': 'Date parameter is required'
        }, status=400)

    try:
        service = Service.objects.get(id=service_id)
        date_obj = dt.strptime(date_str, '%Y-%m-%d').date()
    except Service.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Service not found'
        }, status=404)
    except ValueError:
        return Response({
            'success': False,
            'message': 'Invalid date format. Use YYYY-MM-DD'
        }, status=400)

    # Check if date is in the future
    if date_obj < timezone.now().date():
        return Response({
            'success': False,
            'message': 'Date must be in the future'
        }, status=400)

    # Artist-specific: if date is blocked or admin-booked, no slots
    artist_date = ArtistAvailability.objects.filter(
        artist=service,
        date=date_obj,
        status__in=[ArtistAvailability.STATUS_BLOCKED, ArtistAvailability.STATUS_BOOKED],
    ).first()
    if artist_date:
        return Response({
            'success': False,
            'message': 'Date already unavailable',
            'date': date_str,
            'available_slots': [],
            'booked_slots': [],
        }, status=200)

    # Get existing bookings for this date
    service_options = service.options_set.all()
    booked_times = ServiceBooking.objects.filter(
        service_option__in=service_options,
        booking_date=date_obj,
        status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
    ).values_list('booking_time', flat=True)

    # Generate available time slots (9 AM to 6 PM)
    all_slots = [
        f"{hour:02d}:00:00" for hour in range(9, 19)
    ]

    # Filter out booked slots
    available_slots = [
        slot for slot in all_slots
        if slot not in [str(bt) for bt in booked_times]
    ]

    return Response({
        'success': True,
        'date': date_str,
        'available_slots': available_slots,
        'booked_slots': [str(bt) for bt in booked_times]
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def cancel_booking(request, booking_id):
    """
    Cancel a service booking
    URL: /api/services/bookings/<booking_id>/cancel/
    """
    from django.utils import timezone

    user = request.user
    cancellation_reason = request.data.get('reason', 'User requested cancellation')
    refund_requested = request.data.get('refund_requested', False)

    print(f"ðŸ—‘ï¸ Cancel booking request:")
    print(f"  - Booking ID: {booking_id}")
    print(f"  - User: {user.email}")
    print(f"  - Reason: {cancellation_reason}")
    print(f"  - Refund: {refund_requested}")

    try:
        booking = ServiceBooking.objects.get(id=booking_id, user=user)
    except ServiceBooking.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Booking not found'
        }, status=404)

    # Check if booking can be cancelled
    if booking.status in ['COMPLETED', 'CANCELLED']:
        return Response({
            'success': False,
            'message': f'Cannot cancel a {booking.status.lower()} booking'
        }, status=400)

    # Check if booking date has passed
    if booking.booking_date < timezone.now().date():
        return Response({
            'success': False,
            'message': 'Cannot cancel past bookings'
        }, status=400)

    # Cancel the booking
    booking.status = 'CANCELLED'
    booking.notes = f"{booking.notes}\n\nCancellation Reason: {cancellation_reason}"
    if refund_requested:
        booking.notes = f"{booking.notes}\nRefund Requested: Yes"
    booking.save()

    print(f"âœ… Booking cancelled successfully: {booking_id}")

    # Send notification (optional)
    try:
        Notification.objects.create(
            user=user,
            title='Booking Cancelled',
            body=f'Your booking for {booking.service_option.service.title} on {booking.booking_date.strftime("%B %d, %Y")} has been cancelled.',
            image=None
        )
    except Exception as e:
        print(f"Failed to send notification: {e}")

    return Response({
        'success': True,
        'message': 'Booking cancelled successfully'
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def reschedule_booking(request, booking_id):
    """
    Reschedule a service booking

    URL: /api/services/bookings/<booking_id>/reschedule/
    Body: { "new_date": "YYYY-MM-DD", "new_time": "HH:MM:SS" }
    """
    from django.utils import timezone
    from datetime import datetime as dt

    user = request.user
    new_date = request.data.get('new_date')
    new_time = request.data.get('new_time')

    if not new_date or not new_time:
        return Response({
            'success': False,
            'message': 'New date and time are required'
        }, status=400)

    try:
        booking = ServiceBooking.objects.get(id=booking_id, user=user)
    except ServiceBooking.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Booking not found'
        }, status=404)

    # Check if booking can be rescheduled
    if booking.status in ['COMPLETED', 'CANCELLED', 'IN_PROGRESS']:
        return Response({
            'success': False,
            'message': f'Cannot reschedule a {booking.status.lower()} booking'
        }, status=400)

    # Parse new date
    try:
        new_date_obj = dt.strptime(new_date, '%Y-%m-%d').date()
    except ValueError:
        return Response({
            'success': False,
            'message': 'Invalid date format. Use YYYY-MM-DD'
        }, status=400)

    # Check if new date is in the future
    if new_date_obj < timezone.now().date():
        return Response({
            'success': False,
            'message': 'New date must be in the future'
        }, status=400)

    # Check if new date is available
    conflicting_booking = ServiceBooking.objects.filter(
        service_option=booking.service_option,
        booking_date=new_date_obj,
        status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
    ).exclude(id=booking_id).exists()

    if conflicting_booking:
        return Response({
            'success': False,
            'message': 'Selected date is not available'
        }, status=400)

    # Update booking
    old_date = booking.booking_date
    booking.booking_date = new_date_obj
    booking.booking_time = new_time
    booking.notes = f"{booking.notes}\nRescheduled from {old_date} to {new_date_obj}"
    booking.save()

    # Send notification


    return Response({
        'success': True,
        'message': 'Booking rescheduled successfully',
        'booking': ServiceBookingDetailSerializer(
            booking,
            context={'request': request}
        ).data
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def rate_service_booking(request):
    """
    âœ… UPGRADED: Rate or UPDATE a service booking
    Allows users to update their rating

    URL: /api/services/bookings/rate/
    Body: {
        "booking_id": "uuid",
        "overall_rating": 1-5,
        "review_text": "optional text"
    }
    """
    user = request.user

    # Get data from request
    booking_id = request.data.get('booking_id')
    overall_rating = request.data.get('overall_rating')
    review_text = request.data.get('review_text', '')

    # Validate required fields
    if not booking_id or not overall_rating:
        return Response({
            'success': False,
            'message': 'Booking ID and rating are required'
        }, status=400)

    # Validate rating range
    try:
        rating_int = int(overall_rating)
        if not (1 <= rating_int <= 5):
            return Response({
                'success': False,
                'message': 'Rating must be between 1 and 5'
            }, status=400)
    except (ValueError, TypeError):
        return Response({
            'success': False,
            'message': 'Invalid rating format'
        }, status=400)

    # Get the booking
    try:
        booking = ServiceBooking.objects.select_related(
            'service_option__service'
        ).get(id=booking_id, user=user)
    except ServiceBooking.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Booking not found'
        }, status=404)

    # Check if booking is completed
    if booking.status != 'COMPLETED':
        return Response({
            'success': False,
            'message': 'Can only rate completed bookings'
        }, status=400)

    try:
        # âœ… UPGRADED: Check if updating existing rating
        is_update = booking.rating is not None
        old_rating = booking.rating if is_update else None

        # Store rating in proper fields
        booking.rating = rating_int
        booking.review_text = review_text.strip()
        booking.rated_at = timezone.now()
        booking.save()

        # âœ… UPDATE: Aggregate service rating
        service = booking.service_option.service
        _update_service_rating(service)

        action = "updated" if is_update else "saved"
        print(f"âœ… Rating {action}: {rating_int} stars for {service.title}")
        if is_update:
            print(f"   Previous rating: {old_rating} â†’ New rating: {rating_int}")
        print(f"   Review: {review_text[:50]}..." if review_text else "   No review text")

        return Response({
            'success': True,
            'message': f'Rating {action} successfully',
            'is_update': is_update,
            'rating': {
                'booking_id': str(booking.id),
                'overall_rating': rating_int,
                'review_text': review_text,
                'rated_at': booking.rated_at.isoformat(),
                'service_id': str(service.id),
                'service_name': service.title,
                'previous_rating': old_rating
            }
        })

    except Exception as e:
        print(f"âŒ Error saving rating: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to save rating: {str(e)}'
        }, status=500)


def _update_service_rating(service):
    """
    âœ… NEW: Update service's aggregate rating based on all user ratings
    """
    from django.db.models import Avg, Count

    # Get all rated bookings for this service
    ratings_data = ServiceBooking.objects.filter(
        service_option__service=service,
        rating__isnull=False
    ).aggregate(
        avg_rating=Avg('rating'),
        total_reviews=Count('rating')
    )

    # Update service rating
    service.rating = round(ratings_data['avg_rating'] or 0, 1)
    service.total_reviews = ratings_data['total_reviews'] or 0
    service.save(update_fields=['rating', 'total_reviews'])

    print(f"ðŸ“Š Service rating updated: {service.rating} ({service.total_reviews} reviews)")

@api_view(['POST'])
def forgot_password(request):
    phone = request.data.get('phone')

    if not phone:
        return Response({"success": False, "message": "Phone number is required"}, status=400)

    # Check if user exists
    try:
        user = User.objects.get(phone=phone)
    except User.DoesNotExist:
        return Response({"success": False, "message": "Phone number not registered"}, status=404)

    # Send OTP through SMS
    return send_otp(phone)



@api_view(['POST'])
def verify_forgot_password_otp(request):
    """
    Verify OTP for password reset
    Body: { "phone": "1234567890", "otp": "123456" }
    """
    phone = request.data.get('phone')
    otp = request.data.get('otp')

    if not phone or not otp:
        return Response({
            'success': False,
            'message': 'Phone number and OTP are required'
        }, status=400)

    try:
        otp_obj = Otp.objects.get(phone=phone, verified=False)
    except Otp.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Invalid OTP request'
        }, status=400)

    # Check OTP validity
    if otp_obj.validity.replace(tzinfo=None) < datetime.datetime.utcnow():
        return Response({
            'success': False,
            'message': 'OTP has expired. Please request a new one'
        }, status=400)

    # Verify OTP
    if otp_obj.otp != int(otp):
        return Response({
            'success': False,
            'message': 'Invalid OTP'
        }, status=400)

    # Mark OTP as verified
    otp_obj.verified = True
    otp_obj.save()

    # Generate a password reset token
    reset_token = new_token()
    exp_time = timezone.now() + datetime.timedelta(minutes=15)

    try:
        user = User.objects.get(phone=phone)
        PasswordResetToken.objects.update_or_create(
            user=user,
            defaults={
                'token': reset_token,
                'validity': exp_time
            }
        )
    except User.DoesNotExist:
        return Response({
            'success': False,
            'message': 'User not found'
        }, status=404)

    return Response({
        'success': True,
        'message': 'OTP verified successfully',
        'reset_token': reset_token,
        'phone': phone
    }, status=200)


@api_view(['POST'])
def reset_password(request):
    """
    Reset password with verified token
    Body: {
        "phone": "1234567890",
        "reset_token": "token_from_verify_otp",
        "new_password": "newpassword123"
    }
    """
    phone = request.data.get('phone')
    reset_token = request.data.get('reset_token')
    new_password = request.data.get('new_password')

    if not phone or not reset_token or not new_password:
        return Response({
            'success': False,
            'message': 'Phone number, reset token, and new password are required'
        }, status=400)

    # Validate password length
    if len(new_password) < 6:
        return Response({
            'success': False,
            'message': 'Password must be at least 6 characters long'
        }, status=400)

    try:
        user = User.objects.get(phone=phone)
    except User.DoesNotExist:
        return Response({
            'success': False,
            'message': 'User not found'
        }, status=404)

    # Verify reset token
    try:
        token_obj = PasswordResetToken.objects.get(
            user=user,
            token=reset_token
        )
    except PasswordResetToken.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Invalid reset token'
        }, status=400)

    # Check token validity
    if token_obj.validity.replace(tzinfo=None) < datetime.datetime.utcnow():
        token_obj.delete()
        return Response({
            'success': False,
            'message': 'Reset token has expired. Please request a new one'
        }, status=400)

    # Update password
    user.password = make_password(new_password)
    user.save()

    # Delete the used token and OTP
    token_obj.delete()
    Otp.objects.filter(phone=phone).delete()

    # Optionally, logout user from all devices
    Token.objects.filter(user=user).delete()

    return Response({
        'success': True,
        'message': 'Password reset successfully. Please login with your new password'
    }, status=200)


@api_view(['POST'])
def resend_forgot_password_otp(request):
    """
    Resend OTP for password reset
    Body: { "phone": "1234567890" }
    """
    phone = request.data.get('phone')

    if not phone:
        return Response({
            'success': False,
            'message': 'Phone number is required'
        }, status=400)

    # Check if user exists
    try:
        user = User.objects.get(phone=phone)
    except User.DoesNotExist:
        return Response({
            'success': False,
            'message': 'No account found with this phone number'
        }, status=404)

    # Generate and send new OTP
    otp = randint(100000, 999999)
    validity = timezone.now() + datetime.timedelta(minutes=10)

    Otp.objects.update_or_create(
        phone=phone,
        defaults={
            "otp": otp,
            "verified": False,
            "validity": validity
        }
    )

    print(f"Password Reset OTP (Resent) for {phone}: {otp}")

    return Response({
        'success': True,
        'message': 'OTP resent successfully',
        'phone': phone
    }, status=200)

#TODO############################################################ VENDOR API's###########################################################################################################################

# Vendor Authentication & Profile
@api_view(['POST'])
def vendor_register(request):
    """
    Register a new vendor
    Body: {
        "email": "vendor@example.com",
        "phone": "1234567890",
        "password": "password123",
        "fullname": "Vendor Name",
        "business_name": "Business Name"
    }
    """
    email = request.data.get('email')
    phone = request.data.get('phone')
    password = request.data.get('password')
    fullname = request.data.get('fullname')

    if not all([email, phone, password, fullname]):
        return Response({
            'success': False,
            'message': 'All fields are required'
        }, status=400)

    # Check if vendor already exists
    if User.objects.filter(email=email).exists():
        return Response({
            'success': False,
            'message': 'Email already registered'
        }, status=400)

    if User.objects.filter(phone=phone).exists():
        return Response({
            'success': False,
            'message': 'Phone number already registered'
        }, status=400)

    try:
        # Create vendor user
        user = User.objects.create(
            email=email,
            phone=phone,
            fullname=fullname,
            password=make_password(password)
        )

        # Generate token
        token = new_token()
        fcmtoken = request.data.get('fcmtoken', '')
        Token.objects.create(token=token, user=user, fcmtoken=fcmtoken)

        return Response({
            'success': True,
            'message': 'Vendor registered successfully',
            'token': f'token {token}',
            'user': UserSerializer(user).data
        }, status=201)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Registration failed: {str(e)}'
        }, status=500)



@api_view(['GET'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_get_products(request):
    """
    Get all products for vendor with pagination - Only vendor's own products
    Query params: page, search, category
    """
    vendor = request.user
    page = request.GET.get('page', 1)
    search = request.GET.get('search', '')
    category_id = request.GET.get('category', None)

    # Get products for this vendor only
    products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(vendor=vendor)

    # Apply search filter
    if search:
        products = products.filter(
            Q(title__icontains=search) | Q(description__icontains=search)
        )

    # Apply category filter
    if category_id:
        try:
            products = products.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    # Order by position (category-wise) then creation date
    products = products.order_by('position', '-created_at')

    # Pagination
    paginator = Paginator(products, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Serialize products with null safety
    products_data = []
    for product in page_obj:
        # Get total stock for this product
        total_stock = product.options_set.aggregate(
            total=Sum('quantity')
        )['total'] or 0

        # Get first image with null safety
        first_option = product.options_set.first()
        image_url = None
        if first_option:
            first_image = first_option.images_set.first()
            if first_image and first_image.image:
                try:
                    image_url = request.build_absolute_uri(first_image.image.url)
                except Exception as e:
                    print(f"Error building image URL: {e}")
                    image_url = None

        # Build product data with null safety for all fields
        product_data = {
            'id': str(product.id),
            'title': product.title or '',
            'description': product.description or '',
            'price': product.price if product.price is not None else 0,
            'offer_price': product.offer_price if product.offer_price is not None else 0,
            'delivery_charge': product.delivery_charge if product.delivery_charge is not None else 0,
            'cod': product.cod if product.cod is not None else False,
            'category': {
                'id': product.category.id,
                'name': product.category.name
            } if product.category else None,
            'total_stock': int(total_stock),
            'total_options': product.options_set.count(),
            'image': image_url,
            'created_at': product.created_at.isoformat() if product.created_at else None
        }
        products_data.append(product_data)

    return Response({
        'success': True,
        'products': products_data,
        'pagination': {
            'total_pages': paginator.num_pages,
            'current_page': page_obj.number,
            'total_products': paginator.count,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous()
        }
    })
#################


@api_view(['GET'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_get_product_detail(request, product_id):
    """
    Get detailed product information for editing
    """
    try:
        product = Product.objects.prefetch_related(
            'options_set__images_set'
        ).get(id=product_id)
    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

    # Serialize product with all options and images
    product_data = ProductSerializer(product, context={'request': request}).data

    return Response({
        'success': True,
        'product': product_data
    })


# views.py - Enhanced Product Create/Update APIs

@api_view(['POST'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_create_product(request):
    """
    âœ… UPGRADED: Create product with complete rental pricing support
    Body: {
        "title": "Product Name",
        "description": "Description",
        "category_id": 1,

        # Basic Pricing
        "price": 1000,
        "offer_price": 800,
        "delivery_charge": 50,
        "cod": true,

        # Rental Pricing (Per Day) - Optional
        "rent_price_1_day": 100,
        "rent_price_2_days": 180,
        "rent_price_3_days": 250,
        "rent_price_7_days": 500,
        "rent_price_14_days": 900,
        "rent_price_30_days": 1500,

        # Buy Pricing - Optional
        "buy_price": 5000,
        "buy_offer_price": 4000,

        # Date Booking Settings
        "requires_date_selection": true,
        "max_bookings_per_date": 1
    }
    """
    vendor = request.user
    data = request.data

    # Validate required fields
    required_fields = ['title', 'price', 'category_id']
    missing_fields = [field for field in required_fields if not data.get(field)]

    if missing_fields:
        return Response({
            'success': False,
            'message': f'Missing required fields: {", ".join(missing_fields)}'
        }, status=400)

    # Validate category
    try:
        category = Category.objects.get(id=data['category_id'])
    except Category.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Invalid category'
        }, status=400)

    # Validate and parse prices
    try:
        # Basic pricing
        price = int(data['price'])
        offer_price = int(data.get('offer_price', 0))
        delivery_charge = int(data.get('delivery_charge', 0))

        # Rental pricing
        rent_price_1_day = int(data.get('rent_price_1_day', 0))
        rent_price_2_days = int(data.get('rent_price_2_days', 0))
        rent_price_3_days = int(data.get('rent_price_3_days', 0))
        rent_price_7_days = int(data.get('rent_price_7_days', 0))
        rent_price_14_days = int(data.get('rent_price_14_days', 0))
        rent_price_30_days = int(data.get('rent_price_30_days', 0))

        # Buy pricing
        buy_price = int(data.get('buy_price', 0))
        buy_offer_price = int(data.get('buy_offer_price', 0))

        # Validate price logic
        if price <= 0:
            return Response({
                'success': False,
                'message': 'Price must be greater than 0'
            }, status=400)

        if offer_price > price:
            return Response({
                'success': False,
                'message': 'Offer price cannot be greater than price'
            }, status=400)

        if buy_offer_price > buy_price and buy_price > 0:
            return Response({
                'success': False,
                'message': 'Buy offer price cannot be greater than buy price'
            }, status=400)

        # Validate rental pricing progression (optional but recommended)
        rental_prices = [
            rent_price_1_day, rent_price_2_days, rent_price_3_days,
            rent_price_7_days, rent_price_14_days, rent_price_30_days
        ]

        # Check if any rental price is negative
        if any(p < 0 for p in rental_prices):
            return Response({
                'success': False,
                'message': 'Rental prices cannot be negative'
            }, status=400)

    except (ValueError, TypeError) as e:
        return Response({
            'success': False,
            'message': f'Invalid price format: {str(e)}'
        }, status=400)

    try:
        # Assign last position in this category
        from django.db.models import Max
        last_position = Product.objects.filter(category=category).aggregate(Max('position'))['position__max']
        new_position = (last_position or 0) + 1

        # Create product
        product = Product.objects.create(
            vendor=vendor,
            category=category,
            title=data['title'],
            description=data.get('description', ''),

            # Basic pricing
            price=price,
            offer_price=offer_price,
            delivery_charge=delivery_charge,
            cod=data.get('cod', True),

            # Position (category-wise display order)
            position=new_position,

            # Rental pricing
            rent_price_1_day=rent_price_1_day,
            rent_price_2_days=rent_price_2_days,
            rent_price_3_days=rent_price_3_days,
            rent_price_7_days=rent_price_7_days,
            rent_price_14_days=rent_price_14_days,
            rent_price_30_days=rent_price_30_days,

            # Buy pricing
            buy_price=buy_price,
            buy_offer_price=buy_offer_price,

            # Date booking settings
            requires_date_selection=data.get('requires_date_selection', True),
            max_bookings_per_date=int(data.get('max_bookings_per_date', 1))
        )

        # Build response with all pricing info
        product_data = {
            'id': str(product.id),
            'title': product.title,
            'category': category.name,
            'vendor': vendor.vendor_id,

            # Basic pricing
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,

            # Rental pricing
            'rental_pricing': {
                '1_day': product.rent_price_1_day,
                '2_days': product.rent_price_2_days,
                '3_days': product.rent_price_3_days,
                '7_days': product.rent_price_7_days,
                '14_days': product.rent_price_14_days,
                '30_days': product.rent_price_30_days,
            },

            # Buy pricing
            'buy_pricing': {
                'price': product.buy_price,
                'offer_price': product.buy_offer_price,
            },

            # Booking settings
            'requires_date_selection': product.requires_date_selection,
            'max_bookings_per_date': product.max_bookings_per_date,
        }

        return Response({
            'success': True,
            'message': 'Product created successfully',
            'product': product_data
        }, status=201)

    except Exception as e:
        print(f"âŒ Product creation error: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to create product: {str(e)}'
        }, status=500)


@api_view(['PUT'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_update_product(request, product_id):
    """
    âœ… UPGRADED: Update product with complete rental pricing support
    """
    vendor = request.user
    data = request.data

    try:
        # Ensure product belongs to this vendor
        product = Product.objects.get(id=product_id, vendor=vendor)
    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found or you do not have permission to edit it'
        }, status=404)

    try:
        # Update basic fields
        if 'title' in data:
            product.title = data['title']

        if 'description' in data:
            product.description = data['description']

        # Update category
        if 'category_id' in data:
            try:
                category = Category.objects.get(id=data['category_id'])
                product.category = category
            except Category.DoesNotExist:
                return Response({
                    'success': False,
                    'message': 'Invalid category'
                }, status=400)

        # Update basic pricing
        if 'price' in data:
            price = int(data['price'])
            if price <= 0:
                return Response({
                    'success': False,
                    'message': 'Price must be greater than 0'
                }, status=400)
            product.price = price

        if 'offer_price' in data:
            offer_price = int(data['offer_price'])
            if offer_price > product.price:
                return Response({
                    'success': False,
                    'message': 'Offer price cannot be greater than price'
                }, status=400)
            product.offer_price = offer_price

        if 'delivery_charge' in data:
            product.delivery_charge = int(data['delivery_charge'])

        if 'cod' in data:
            product.cod = data['cod']

        # Update rental pricing
        if 'rent_price_1_day' in data:
            product.rent_price_1_day = int(data['rent_price_1_day'])

        if 'rent_price_2_days' in data:
            product.rent_price_2_days = int(data['rent_price_2_days'])

        if 'rent_price_3_days' in data:
            product.rent_price_3_days = int(data['rent_price_3_days'])

        if 'rent_price_7_days' in data:
            product.rent_price_7_days = int(data['rent_price_7_days'])

        if 'rent_price_14_days' in data:
            product.rent_price_14_days = int(data['rent_price_14_days'])

        if 'rent_price_30_days' in data:
            product.rent_price_30_days = int(data['rent_price_30_days'])

        # Update buy pricing
        if 'buy_price' in data:
            buy_price = int(data['buy_price'])
            product.buy_price = buy_price

        if 'buy_offer_price' in data:
            buy_offer_price = int(data['buy_offer_price'])
            if buy_offer_price > product.buy_price and product.buy_price > 0:
                return Response({
                    'success': False,
                    'message': 'Buy offer price cannot be greater than buy price'
                }, status=400)
            product.buy_offer_price = buy_offer_price

        # Update booking settings
        if 'requires_date_selection' in data:
            product.requires_date_selection = data['requires_date_selection']

        if 'max_bookings_per_date' in data:
            product.max_bookings_per_date = int(data['max_bookings_per_date'])

        if 'position' in data:
            product.position = int(data['position'])

        # Save product
        product.save()

        # Build response
        product_data = ProductSerializer(product, context={'request': request}).data

        return Response({
            'success': True,
            'message': 'Product updated successfully',
            'product': product_data
        })

    except (ValueError, TypeError) as e:
        return Response({
            'success': False,
            'message': f'Invalid data format: {str(e)}'
        }, status=400)
    except Exception as e:
        print(f"âŒ Product update error: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to update product: {str(e)}'
        }, status=500)

@api_view(['DELETE'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_delete_product(request, product_id):
    """
    Delete product - Only vendor's own products
    """
    vendor = request.user

    try:
        # Ensure product belongs to this vendor
        product = Product.objects.get(id=product_id, vendor=vendor)
    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found or you do not have permission to delete it'
        }, status=404)

    try:
        product_title = product.title
        product.delete()

        return Response({
            'success': True,
            'message': f'Product "{product_title}" deleted successfully'
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to delete product: {str(e)}'
        }, status=500)

# Product Option Management
# views.py - Add these UPGRADED endpoints

@api_view(['POST'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_create_product_option(request):
    """
    âœ… UPGRADED: Create product option with complete rental pricing support
    Body: {
        "product_id": "uuid",
        "option": "Size M / Color Red",
        "quantity": 100,

        # âœ… NEW: Standard Pricing (Optional - overrides product pricing)
        "option_price": 1000,
        "option_offer_price": 800,

        # âœ… NEW: Rental Pricing (Per Duration) - Optional
        "option_rent_1_day": 100,
        "option_rent_2_days": 180,
        "option_rent_3_days": 250,
        "option_rent_7_days": 500,
        "option_rent_14_days": 900,
        "option_rent_30_days": 1500,

        # âœ… NEW: Buy Pricing - Optional
        "option_buy_price": 5000,
        "option_buy_offer_price": 4000,

        # âœ… NEW: Auto-calculation toggle
        "auto_calculate_rental_prices": true
    }
    """
    vendor = request.user
    data = request.data

    product_id = data.get('product_id')
    option = data.get('option', '')
    quantity = data.get('quantity', 0)

    if not product_id:
        return Response({
            'success': False,
            'message': 'Product ID is required'
        }, status=400)

    try:
        # Verify product belongs to vendor
        product = Product.objects.get(id=product_id, vendor=vendor)
    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found or access denied'
        }, status=404)

    # Validate and parse quantity
    try:
        quantity = int(quantity)
        if quantity < 0:
            return Response({
                'success': False,
                'message': 'Quantity cannot be negative'
            }, status=400)
    except (ValueError, TypeError):
        return Response({
            'success': False,
            'message': 'Invalid quantity format'
        }, status=400)

    try:
        # Parse pricing fields (all optional)
        option_price = int(data.get('option_price', 0))
        option_offer_price = int(data.get('option_offer_price', 0))

        # Rental pricing
        option_rent_1_day = int(data.get('option_rent_1_day', 0))
        option_rent_2_days = int(data.get('option_rent_2_days', 0))
        option_rent_3_days = int(data.get('option_rent_3_days', 0))
        option_rent_7_days = int(data.get('option_rent_7_days', 0))
        option_rent_14_days = int(data.get('option_rent_14_days', 0))
        option_rent_30_days = int(data.get('option_rent_30_days', 0))

        # Buy pricing
        option_buy_price = int(data.get('option_buy_price', 0))
        option_buy_offer_price = int(data.get('option_buy_offer_price', 0))

        # Auto-calculation toggle
        auto_calculate = data.get('auto_calculate_rental_prices', True)
        is_rent_available = data.get('is_rent_available', data.get('rent_available', True))
        is_buy_available = data.get('is_buy_available', data.get('buy_available', True))

        # Validate pricing logic
        if option_price > 0 and option_offer_price > option_price:
            return Response({
                'success': False,
                'message': 'Offer price cannot be greater than price'
            }, status=400)

        if option_buy_price > 0 and option_buy_offer_price > option_buy_price:
            return Response({
                'success': False,
                'message': 'Buy offer price cannot be greater than buy price'
            }, status=400)

    except (ValueError, TypeError) as e:
        return Response({
            'success': False,
            'message': f'Invalid price format: {str(e)}'
        }, status=400)

    try:
        # Create product option with all pricing fields
        product_option = ProductOption.objects.create(
            product=product,
            option=option,
            quantity=quantity,

            # Standard pricing
            option_price=option_price,
            option_offer_price=option_offer_price,

            # Rental pricing
            option_rent_1_day=option_rent_1_day,
            option_rent_2_days=option_rent_2_days,
            option_rent_3_days=option_rent_3_days,
            option_rent_7_days=option_rent_7_days,
            option_rent_14_days=option_rent_14_days,
            option_rent_30_days=option_rent_30_days,

            # Buy pricing
            option_buy_price=option_buy_price,
            option_buy_offer_price=option_buy_offer_price,

            # Auto-calculation
            auto_calculate_rental_prices=auto_calculate,
            is_rent_available=is_rent_available,
            is_buy_available=is_buy_available,
        )

        print(f"âœ… Product option created: {product_option.id}")
        print(f"   - Option: {product_option.option}")
        print(f"   - Quantity: {product_option.quantity}")
        print(f"   - Auto-calc: {product_option.auto_calculate_rental_prices}")

        # Build comprehensive response
        response_data = {
            'id': str(product_option.id),
            'option': product_option.option,
            'quantity': product_option.quantity,
            'is_rent_available': product_option.is_rent_available,
            'is_buy_available': product_option.is_buy_available,
            'product': str(product_option.product.id),

            # Standard pricing
            'option_price': product_option.option_price,
            'option_offer_price': product_option.option_offer_price,
            'effective_price': product_option.get_price(),
            'effective_offer_price': product_option.get_offer_price(),

            # Complete rental pricing (calculated or custom)
            'rental_pricing': product_option.get_rental_pricing_dict(),

            # Auto-calculation status
            'auto_calculate_rental_prices': product_option.auto_calculate_rental_prices,
            'has_custom_pricing': product_option.has_custom_pricing(),

            # Pricing breakdown
            'pricing_breakdown': {
                'base_daily_rate': product_option.base_daily_rate,
                'breakeven_days': product_option.get_breakeven_point(),
                'price_per_day_1d': product_option.get_price_per_day('1_day'),
            }
        }

        return Response({
            'success': True,
            'message': 'Product option created successfully',
            'product_option': response_data
        }, status=201)

    except Exception as e:
        print(f"âŒ Failed to create product option: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to create product option: {str(e)}'
        }, status=500)


@api_view(['PUT'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_update_product_option(request, option_id):
    """
    âœ… UPGRADED: Update product option with complete rental pricing support
    All fields are optional - only send what you want to update
    """
    vendor = request.user
    data = request.data

    try:
        # Verify option belongs to vendor's product
        product_option = ProductOption.objects.select_related('product').get(
            id=option_id,
            product__vendor=vendor
        )
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product option not found or access denied'
        }, status=404)

    try:
        # Update basic fields
        if 'option' in data:
            product_option.option = data['option']

        if 'quantity' in data:
            quantity = int(data['quantity'])
            if quantity < 0:
                return Response({
                    'success': False,
                    'message': 'Quantity cannot be negative'
                }, status=400)
            product_option.quantity = quantity
        if 'is_rent_available' in data or 'rent_available' in data:
            product_option.is_rent_available = data.get('is_rent_available', data.get('rent_available'))
        if 'is_buy_available' in data or 'buy_available' in data:
            product_option.is_buy_available = data.get('is_buy_available', data.get('buy_available'))

        # Update standard pricing
        if 'option_price' in data:
            product_option.option_price = int(data['option_price'])

        if 'option_offer_price' in data:
            offer_price = int(data['option_offer_price'])
            if product_option.option_price > 0 and offer_price > product_option.option_price:
                return Response({
                    'success': False,
                    'message': 'Offer price cannot be greater than price'
                }, status=400)
            product_option.option_offer_price = offer_price

        # Update rental pricing
        if 'option_rent_1_day' in data:
            product_option.option_rent_1_day = int(data['option_rent_1_day'])
        if 'option_rent_2_days' in data:
            product_option.option_rent_2_days = int(data['option_rent_2_days'])
        if 'option_rent_3_days' in data:
            product_option.option_rent_3_days = int(data['option_rent_3_days'])
        if 'option_rent_7_days' in data:
            product_option.option_rent_7_days = int(data['option_rent_7_days'])
        if 'option_rent_14_days' in data:
            product_option.option_rent_14_days = int(data['option_rent_14_days'])
        if 'option_rent_30_days' in data:
            product_option.option_rent_30_days = int(data['option_rent_30_days'])

        # Update buy pricing
        if 'option_buy_price' in data:
            product_option.option_buy_price = int(data['option_buy_price'])

        if 'option_buy_offer_price' in data:
            buy_offer = int(data['option_buy_offer_price'])
            if product_option.option_buy_price > 0 and buy_offer > product_option.option_buy_price:
                return Response({
                    'success': False,
                    'message': 'Buy offer price cannot be greater than buy price'
                }, status=400)
            product_option.option_buy_offer_price = buy_offer

        # Update auto-calculation toggle
        if 'auto_calculate_rental_prices' in data:
            product_option.auto_calculate_rental_prices = data['auto_calculate_rental_prices']

        # Save changes (triggers auto-calculation if enabled)
        product_option.save()

        print(f"âœ… Product option updated: {product_option.id}")

        # Build comprehensive response
        response_data = {
            'id': str(product_option.id),
            'option': product_option.option,
            'quantity': product_option.quantity,
            'is_rent_available': product_option.is_rent_available,
            'is_buy_available': product_option.is_buy_available,
            'product': str(product_option.product.id),

            # Standard pricing
            'option_price': product_option.option_price,
            'option_offer_price': product_option.option_offer_price,
            'effective_price': product_option.get_price(),
            'effective_offer_price': product_option.get_offer_price(),

            # Complete rental pricing
            'rental_pricing': product_option.get_rental_pricing_dict(),

            # Auto-calculation status
            'auto_calculate_rental_prices': product_option.auto_calculate_rental_prices,
            'has_custom_pricing': product_option.has_custom_pricing(),

            # Images
            'images': [
                {
                    'id': img.id,
                    'position': img.position,
                    'url': request.build_absolute_uri(img.image.url) if img.image else None
                }
                for img in product_option.images_set.all().order_by('position')
            ]
        }

        return Response({
            'success': True,
            'message': 'Product option updated successfully',
            'product_option': response_data
        })

    except (ValueError, TypeError) as e:
        return Response({
            'success': False,
            'message': f'Invalid data format: {str(e)}'
        }, status=400)
    except Exception as e:
        print(f"âŒ Failed to update product option: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to update product option: {str(e)}'
        }, status=500)

@api_view(['DELETE'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_delete_product_option(request, option_id):
    """
    Delete a product option
    """
    try:
        product_option = ProductOption.objects.get(id=option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product option not found'
        }, status=404)

    try:
        option_name = product_option.option
        product_option.delete()

        return Response({
            'success': True,
            'message': f'Product option "{option_name}" deleted successfully'
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to delete product option: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
@parser_classes([MultiPartParser, FormParser])
def vendor_upload_product_image(request):
    """
    Upload image for a product option
    Form Data:
        - product_option_id: uuid
        - position: int
        - image: file
    """
    product_option_id = request.data.get('product_option_id')
    position = request.data.get('position', 0)
    image = request.FILES.get('image')

    if not product_option_id or not image:
        return Response({
            'success': False,
            'message': 'Product option ID and image are required'
        }, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product option not found'
        }, status=404)

    # Validate file size (5MB max)
    if image.size > 5 * 1024 * 1024:
        return Response({
            'success': False,
            'message': 'Image file too large (max 5MB)'
        }, status=400)

    try:
        position = int(position)
    except (ValueError, TypeError):
        position = 0

    try:
        # Create product image
        product_image = ProductImage.objects.create(
            position=position,
            image=image,
            product_option=product_option
        )

        image_url = request.build_absolute_uri(product_image.image.url)

        return Response({
            'success': True,
            'message': 'Image uploaded successfully',
            'image': {
                'id': product_image.id,
                'position': product_image.position,
                'url': image_url,
                'product_option': str(product_option.id)
            }
        }, status=201)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to upload image: {str(e)}'
        }, status=500)


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_delete_product_image(request, image_id):
    """
    Delete a product image
    """
    try:
        product_image = ProductImage.objects.get(id=image_id)
    except ProductImage.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Image not found'
        }, status=404)

    try:
        # Delete the image file from storage
        if product_image.image:
            product_image.image.delete()

        product_image.delete()

        return Response({
            'success': True,
            'message': 'Image deleted successfully'
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to delete image: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_get_categories(request):
    """
    Get all categories for dropdown - Vendor access
    """
    vendor = request.user
    # Support multi-pincode vendors via Vendor.serviceable_locations.
    # Fallback to legacy Vendor.pincode if no locations selected.
    pincodes = []
    try:
        if getattr(vendor, 'serviceable_locations', None) is not None:
            pincodes = list(
                vendor.serviceable_locations.filter(is_active=True).values_list('pincode', flat=True)
            )
    except Exception:
        pincodes = []

    if not pincodes:
        pincode = (getattr(vendor, 'pincode', '') or '').strip()
        if pincode:
            pincodes = [pincode]

    categories = Category.objects.all().order_by('position')

    # If vendor has pincodes, show ONLY categories explicitly enabled for any of those pincodes.
    # This matches admin intent: selecting locations decides where category appears.
    if pincodes:
        categories = categories.filter(
            location_availability__location__pincode__in=pincodes,
            location_availability__location__is_active=True,
            location_availability__is_available=True,
        ).distinct().order_by('position')

    categories_data = CategorySerializer(
        categories,
        many=True,
        context={'request': request}
    ).data

    return Response({
        'success': True,
        'categories': categories_data
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_bulk_update_stock(request):
    """
    Bulk update stock for multiple product options
    Body: {
        "updates": [
            {"option_id": "uuid", "quantity": 100},
            {"option_id": "uuid", "quantity": 50}
        ]
    }
    """
    updates = request.data.get('updates', [])

    if not updates:
        return Response({
            'success': False,
            'message': 'No updates provided'
        }, status=400)

    updated_count = 0
    errors = []

    try:
        with transaction.atomic():
            for update in updates:
                option_id = update.get('option_id')
                quantity = update.get('quantity')

                if not option_id or quantity is None:
                    errors.append(f'Missing option_id or quantity in update')
                    continue

                try:
                    product_option = ProductOption.objects.get(id=option_id)
                    product_option.quantity = int(quantity)
                    product_option.save()
                    updated_count += 1
                except ProductOption.DoesNotExist:
                    errors.append(f'Product option {option_id} not found')
                except (ValueError, TypeError):
                    errors.append(f'Invalid quantity for option {option_id}')

        return Response({
            'success': True,
            'message': f'Successfully updated {updated_count} product options',
            'updated_count': updated_count,
            'errors': errors if errors else None
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Bulk update failed: {str(e)}'
        }, status=500)


# views.py - Update product_details_with_dates

@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Allow guest access
def product_details_with_dates(request, product_id):
    """
    ✅ COMPLETE: Enhanced product details with per-option pricing and review text
    ✅ NOW WORKS FOR GUESTS

    URL: /api/product/<product_id>/details-with-dates/
    Query Params: reviews_page (optional, default: 1)

    Returns:
        - Complete product information
        - All options with individual pricing
        - Rental pricing for each duration
        - Images for each option
        - Calendar availability data
        - Related products
        - ✅ NEW: Customer reviews with text
        - ✅ NEW: Review pagination
        - Cart/wishlist status (if authenticated)
    """
    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    print(f'📦 Product details: {product_id} | Guest: {not is_authenticated}')

    try:
        product = Product.objects.select_related('category', 'vendor').prefetch_related(
            'options_set__images_set'
        ).get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=404)

    # ✅ Get all product options with their individual pricing
    options_data = []
    for option in product.options_set.all():
        option_images = []
        for img in option.images_set.order_by('position'):
            img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
            option_images.append({
                'position': img.position,
                'image': img_url,
                'product_option_id': str(option.id)
            })

        option_data = {
            'id': str(option.id),
            'option': option.option,
            'quantity': option.quantity,
            'in_stock': option.quantity > 0,
            'images': option_images,
            # ✅ Only check cart/wishlist if authenticated
            'in_cart': user.cart.filter(id=option.id).exists() if is_authenticated else False,
            'in_wishlist': user.wishlist.filter(id=option.id).exists() if is_authenticated else False,
            # ✅ Availability fields
            'rent_available': option.is_rent_available,
            'buy_available': option.is_buy_available,
            # ✅ Per-option pricing
            'pricing': {
                'price': option.get_price(),
                'offer_price': option.get_offer_price(),
                'rental_pricing': option.get_rental_pricing_dict(),
            }
        }
        options_data.append(option_data)

    # Calculate ratings
    total_ratings = product.star_5 + product.star_4 + product.star_3 + product.star_2 + product.star_1
    if total_ratings > 0:
        average_rating = (
                                 (product.star_5 * 5) + (product.star_4 * 4) + (product.star_3 * 3) +
                                 (product.star_2 * 2) + (product.star_1 * 1)
                         ) / total_ratings
        average_rating = round(average_rating, 1)
    else:
        average_rating = 0

    # ✅ NEW: Get reviews with pagination
    reviews_page = int(request.GET.get('reviews_page', 1))
    reviews_data = _get_product_reviews(product, page=reviews_page)

    print(f'💬 Found {reviews_data["total_reviews"]} reviews, showing page {reviews_data["current_page"]}')

    # Calculate discount
    discount_percentage = 0
    effective_buy_price = product.get_buy_offer_price() or product.get_buy_price()
    if effective_buy_price and effective_buy_price < product.price:
        discount_percentage = round(((product.price - effective_buy_price) / product.price) * 100)
    elif product.offer_price > 0 and product.offer_price < product.price:
        discount_percentage = round(((product.price - product.offer_price) / product.price) * 100)

    # Get main image
    main_image = None
    first_option = product.options_set.first()
    if first_option:
        first_image = first_option.images_set.first()
        if first_image and request:
            main_image = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            main_image = first_image.image.url

    # ✅ Product-level rental pricing (fallback/default)
    product_rental_pricing = {
        'rent': {
            '1_day': product.get_rental_price('1_day'),
            '2_days': product.get_rental_price('2_days'),
            '3_days': product.get_rental_price('3_days'),
            '7_days': product.get_rental_price('7_days'),
            '14_days': product.get_rental_price('14_days'),
            '30_days': product.get_rental_price('30_days'),
        },
        'buy': {
            'price': product.get_buy_price(),
            'offer_price': product.get_buy_offer_price(),
        }
    }

    # Get vendor information
    vendor_info = None
    if product.vendor:
        vendor_info = {
            'vendor_id': product.vendor.vendor_id,
            'name': product.vendor.name,
            'phone': product.vendor.phone,
            'email': product.vendor.email,
            'business_address': product.vendor.business_address,
        }

    trial_available = True
    if product.vendor is not None:
        trial_available = bool(getattr(product.vendor, 'trial_enabled', False))

    # Get booked dates with calendar structure
    calendar_data = _get_calendar_data(product)

    # Check stock
    in_stock = product.options_set.filter(quantity__gt=0).exists()

    # Get best price for display
    first_option = product.options_set.first()
    best_price = first_option.get_offer_price() if first_option else product.offer_price
    if best_price == 0:
        best_price = first_option.get_price() if first_option else product.price

    # ✅ Build complete product data with reviews
    product_data = {
        'id': str(product.id),
        'title': product.title,
        'description': product.description,
        'price': product.price,
        'offer_price': product.offer_price,
        'cutted_price': product.price if discount_percentage > 0 else None,
        'effective_price': best_price,
        'delivery_charge': product.delivery_charge,
        'cod_available': product.cod,
        'discount_percentage': discount_percentage,
        'main_image': main_image,
        'category': {
            'id': product.category.id,
            'name': product.category.name,
        } if product.category else None,
        'in_stock': in_stock,
        'created_at': product.created_at.isoformat(),
        'updated_at': product.updated_at.isoformat(),
        'requires_date_selection': product.requires_date_selection,
        'max_bookings_per_date': product.max_bookings_per_date,
        'rental_pricing': product_rental_pricing,
        'vendor_info': vendor_info,
        'trial_available': trial_available,
        'calendar_data': calendar_data,

        # ✅ ENHANCED: Ratings with review text
        'ratings': {
            'average_rating': average_rating,
            'total_reviews': total_ratings,
            'star_5': product.star_5,
            'star_4': product.star_4,
            'star_3': product.star_3,
            'star_2': product.star_2,
            'star_1': product.star_1,
            # ✅ NEW: Customer reviews with text
            'reviews': reviews_data['reviews'],
            'reviews_pagination': {
                'current_page': reviews_data['current_page'],
                'total_pages': reviews_data['total_pages'],
                'has_next': reviews_data['has_next'],
                'has_previous': reviews_data['has_previous'],
                'total_reviews': reviews_data['total_reviews'],
            }
        },

        'options': options_data,
        'user_status': {
            'has_in_cart': any(option['in_cart'] for option in options_data),
            'has_in_wishlist': any(option['in_wishlist'] for option in options_data),
            'is_authenticated': is_authenticated,
        }
    }

    # Get related products
    related_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        category=product.category,
        options_set__quantity__gt=0
    ).exclude(id=product.id).distinct()[:6]

    related_products_data = []
    for related_product in related_products:
        first_option = related_product.options_set.first()
        image_url = None
        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        related_data = {
            'id': str(related_product.id),
            'title': related_product.title,
            'price': related_product.price,
            'offer_price': related_product.offer_price,
            'image': image_url,
        }
        related_products_data.append(related_data)

    print(f'✅ Loaded product details with {len(options_data)} options and {len(reviews_data["reviews"])} reviews')

    return Response({
        'product': product_data,
        'related_products': related_products_data,
        'success': True
    })
def _get_calendar_data(product):
    """
    ✅ UPGRADED: Generate enhanced calendar data for next 6 MONTHS with booking availability
    Shows exact quantity available for each date with proper color coding

    Args:
        product: Product instance

    Returns:
        dict: Calendar data with 6 months of availability info
        None: If product doesn't require date selection
    """
    from django.utils import timezone
    from datetime import date, timedelta
    from django.db.models import Sum
    from backend.models import ProductBooking

    # Skip if product doesn't require date selection
    if not product.requires_date_selection:
        return None

    today = timezone.now().date()
    calendar_months = []

    # ✅ UPGRADED: Generate data for next 6 MONTHS
    for month_offset in range(6):
        # Calculate which month to process
        target_date = today + timedelta(days=30 * month_offset)
        year = target_date.year
        month = target_date.month

        # Get first and last day of the month
        first_day = date(year, month, 1)

        # Calculate last day of month
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)

        # Ensure we don't go before today
        if first_day < today:
            first_day = today

        # Skip this month if it's entirely in the past
        if last_day < today:
            continue

        # Get all confirmed/pending bookings for this month
        bookings = ProductBooking.objects.filter(
            product=product,
            booking_date__gte=first_day,
            booking_date__lte=last_day,
            status__in=['PENDING', 'CONFIRMED']
        ).values('booking_date').annotate(
            total_booked=Sum('quantity_booked')
        )

        # Create lookup dictionary for quick access
        booking_dict = {
            booking['booking_date']: booking['total_booked']
            for booking in bookings
        }

        # Generate days array for this month
        days = []
        current = first_day

        while current <= last_day:
            # Skip dates before today
            if current < today:
                current += timedelta(days=1)
                continue

            # Get total bookings for this date
            total_booked = booking_dict.get(current, 0)

            # Calculate max capacity for this date
            max_per_date = product.max_bookings_per_date if product.max_bookings_per_date > 0 else 999999

            # Calculate available quantity
            available = max_per_date - total_booked

            # Ensure available quantity is never negative
            available = max(0, available)

            # ✅ ENHANCED: Add color category for frontend
            color_category = 'red'  # Default: fully booked
            if available == 0:
                color_category = 'red'  # Fully booked
            elif available >= 1 and available <= 2:
                color_category = 'orange'  # Low stock
            elif available >= 3 and available <= 5:
                color_category = 'lightGreen'  # Medium stock
            else:
                color_category = 'green'  # High stock

            # Create day data
            day_data = {
                'date': current.strftime('%Y-%m-%d'),
                'day': current.day,
                'is_available': available > 0,
                'available_quantity': available,
                'is_today': current == today,
                'day_of_week': current.strftime('%a'),
                'is_weekend': current.weekday() >= 5,
                'total_booked': total_booked,
                'max_capacity': max_per_date if max_per_date != 999999 else None,
                'color_category': color_category,  # ✅ NEW: Color hint for frontend
            }

            days.append(day_data)
            current += timedelta(days=1)

        # Only add month if it has days to show
        if days:
            month_data = {
                'year': year,
                'month': month,
                'month_name': date(year, month, 1).strftime('%B'),
                'month_name_short': date(year, month, 1).strftime('%b'),
                'days': days,
                'total_days': len(days),
                'available_days': sum(1 for day in days if day['is_available']),
                'fully_booked_days': sum(1 for day in days if not day['is_available']),
            }
            calendar_months.append(month_data)

    # Calculate overall stats
    total_available_days = sum(month['available_days'] for month in calendar_months)
    total_booked_days = sum(month['fully_booked_days'] for month in calendar_months)

    # ✅ UPGRADED: Return 6 months of data
    return {
        'months': calendar_months,
        'available_from': today.strftime('%Y-%m-%d'),
        'available_until': (today + timedelta(days=180)).strftime('%Y-%m-%d'),  # 6 months
        'total_available_days': total_available_days,
        'total_booked_days': total_booked_days,
        'max_bookings_per_date': product.max_bookings_per_date,
        'product_title': product.title,
        'total_months': len(calendar_months),  # ✅ NEW
    }

@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def check_date_availability(request):
    """
    Check if a specific date is available for booking
    Body: {
        "product_id": "uuid",
        "product_option_id": "uuid" (optional),
        "date": "YYYY-MM-DD",
        "quantity": 1
    }
    """
    product_id = request.data.get('product_id')
    product_option_id = request.data.get('product_option_id')
    date_str = request.data.get('date')
    quantity = request.data.get('quantity', 1)

    if not product_id or not date_str:
        return Response({
            'success': False,
            'message': 'Product ID and date are required'
        }, status=400)

    try:
        product = Product.objects.get(id=product_id)
        booking_date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)
    except ValueError:
        return Response({
            'success': False,
            'message': 'Invalid date format. Use YYYY-MM-DD'
        }, status=400)

    # Check if date is in the past
    if booking_date < timezone.now().date():
        return Response({
            'success': False,
            'available': False,
            'message': 'Cannot book dates in the past'
        }, status=400)

    # If product doesn't require date selection
    if not product.requires_date_selection:
        return Response({
            'success': True,
            'available': True,
            'message': 'This product does not require date selection'
        })

    # Get total bookings for this date
    bookings_filter = Q(
        product=product,
        booking_date=booking_date,
        status__in=['PENDING', 'CONFIRMED']
    )

    if product_option_id:
        bookings_filter &= Q(product_option_id=product_option_id)

    total_booked = ProductBooking.objects.filter(bookings_filter).aggregate(
        total=Sum('quantity_booked')
    )['total'] or 0

    # Check availability
    max_per_date = product.max_bookings_per_date if product.max_bookings_per_date > 0 else 999999
    available_quantity = max_per_date - total_booked

    is_available = available_quantity >= quantity

    return Response({
        'success': True,
        'available': is_available,
        'date': date_str,
        'available_quantity': max(0, available_quantity),
        'requested_quantity': quantity,
        'message': 'Date is available' if is_available else f'Only {available_quantity} slots available for this date'
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_cart_with_date(request):
    """
    Enhanced add to cart that handles date selection and rental information
    âœ… UPGRADED: Uses database rental pricing values and handles duplicates
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')
    quantity = request.data.get('quantity', 1)
    selected_date_str = request.data.get('selected_date')
    rental_type = request.data.get('rental_type', 'rent')
    rental_duration = request.data.get('rental_duration', '1_day')

    print(f"ðŸ›’ Add to cart request:")
    print(f"  - Product Option: {product_option_id}")
    print(f"  - Rental Type: {rental_type}")
    print(f"  - Duration: {rental_duration}")
    print(f"  - Selected Date: {selected_date_str}")
    print(f"  - Quantity: {quantity}")

    if not product_option_id:
        return Response({
            'success': False,
            'message': 'Product option ID is required'
        }, status=400)

    try:
        product_option = ProductOption.objects.select_related('product').get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product option not found'
        }, status=404)

    product = product_option.product
    selected_date = None

    # âœ… FIXED: Only check date if rental type is 'rent'
    if product.requires_date_selection and rental_type == 'rent':
        if not selected_date_str:
            return Response({
                'success': False,
                'message': 'This product requires date selection for rental'
            }, status=400)

        try:
            selected_date = timezone.datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({
                'success': False,
                'message': 'Invalid date format. Use YYYY-MM-DD'
            }, status=400)

        # Check if date is in the past
        if selected_date < timezone.now().date():
            return Response({
                'success': False,
                'message': 'Cannot select dates in the past'
            }, status=400)

        # Check date availability
        total_booked = ProductBooking.objects.filter(
            product=product,
            booking_date=selected_date,
            status__in=['PENDING', 'CONFIRMED']
        ).aggregate(total=Sum('quantity_booked'))['total'] or 0

        max_per_date = product.max_bookings_per_date if product.max_bookings_per_date > 0 else 999999
        available = max_per_date - total_booked

        if available < quantity:
            return Response({
                'success': False,
                'message': f'Only {available} slots available for the selected date'
            }, status=400)

    # âœ… Calculate rental price using database values
    rental_price = _calculate_rental_price(product_option, rental_type, rental_duration)

    # Check stock
    if product_option.quantity < quantity:
        return Response({
            'success': False,
            'message': 'Insufficient stock'
        }, status=400)

    # âœ… FIXED: Handle duplicates by updating quantity
    try:
        if rental_type == 'buy':
            # For 'buy' type, check if item already exists
            existing_item = CartItem.objects.filter(
                user=user,
                product_option=product_option,
                rental_type='buy'
            ).first()

            if existing_item:
                # âœ… UPDATE: Increase quantity instead of returning error
                old_quantity = existing_item.quantity
                existing_item.quantity += quantity

                # Check if new quantity exceeds stock
                if existing_item.quantity > product_option.quantity:
                    return Response({
                        'success': False,
                        'message': f'Cannot add {quantity} more. Only {product_option.quantity - old_quantity} available'
                    }, status=400)

                existing_item.save()

                print(f"âœ… Cart item quantity updated: {old_quantity} â†’ {existing_item.quantity}")

                return Response({
                    'success': True,
                    'message': f'Cart updated - Quantity increased to {existing_item.quantity}',
                    'cart_count': CartItem.objects.filter(user=user).count(),
                    'rental_info': {
                        'type': rental_type,
                        'duration': None,
                        'price': rental_price,
                        'start_date': None,
                        'old_quantity': old_quantity,
                        'new_quantity': existing_item.quantity
                    }
                })

            # Create new cart item for purchase
            cart_item = CartItem.objects.create(
                user=user,
                product_option=product_option,
                quantity=quantity,
                rental_type='buy',
                rental_duration='',
                rental_price=rental_price,
                selected_date=None
            )
            created = True

        else:
            # For 'rent' type, check if exact same rental already exists
            existing_item = CartItem.objects.filter(
                user=user,
                product_option=product_option,
                selected_date=selected_date,
                rental_type='rent',
                rental_duration=rental_duration
            ).first()

            if existing_item:
                # âœ… UPDATE: Increase quantity instead of error
                old_quantity = existing_item.quantity
                existing_item.quantity += quantity

                # Check if new quantity exceeds stock
                if existing_item.quantity > product_option.quantity:
                    return Response({
                        'success': False,
                        'message': f'Cannot add {quantity} more. Only {product_option.quantity - old_quantity} available'
                    }, status=400)

                # Check date availability for new total quantity
                total_booked = ProductBooking.objects.filter(
                    product=product,
                    booking_date=selected_date,
                    status__in=['PENDING', 'CONFIRMED']
                ).aggregate(total=Sum('quantity_booked'))['total'] or 0

                max_per_date = product.max_bookings_per_date if product.max_bookings_per_date > 0 else 999999
                available = max_per_date - total_booked

                if available < existing_item.quantity:
                    return Response({
                        'success': False,
                        'message': f'Cannot add {quantity} more. Only {available - old_quantity} slots available for this date'
                    }, status=400)

                existing_item.save()

                print(f"âœ… Cart item quantity updated: {old_quantity} â†’ {existing_item.quantity}")

                return Response({
                    'success': True,
                    'message': f'Cart updated - Quantity increased to {existing_item.quantity}',
                    'cart_count': CartItem.objects.filter(user=user).count(),
                    'rental_info': {
                        'type': rental_type,
                        'duration': rental_duration,
                        'price': rental_price,
                        'start_date': selected_date.strftime('%Y-%m-%d') if selected_date else None,
                        'old_quantity': old_quantity,
                        'new_quantity': existing_item.quantity
                    }
                })

            # Create new cart item for rental
            cart_item = CartItem.objects.create(
                user=user,
                product_option=product_option,
                quantity=quantity,
                rental_type='rent',
                rental_duration=rental_duration,
                rental_price=rental_price,
                selected_date=selected_date
            )
            created = True

        # Also add to the old ManyToMany cart for compatibility
        if not user.cart.filter(id=product_option.id).exists():
            user.cart.add(product_option)

        print(f"âœ… Cart item created successfully: ID={cart_item.id}")

        return Response({
            'success': True,
            'message': f'Added to cart - {rental_type.title()} for {rental_duration.replace("_", " ") if rental_type == "rent" else "Purchase"}',
            'cart_count': CartItem.objects.filter(user=user).count(),
            'rental_info': {
                'type': rental_type,
                'duration': rental_duration if rental_type == 'rent' else None,
                'price': rental_price,
                'start_date': selected_date.strftime('%Y-%m-%d') if selected_date else None,
                'quantity': quantity
            }
        })

    except Exception as e:
        print(f"ðŸ’¥ Error adding to cart: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to add to cart: {str(e)}'
        }, status=500)


def _calculate_rental_price(product_option, rental_type, rental_duration):
    """
    âœ… UPGRADED: Calculate rental price using database values from ProductOption
    Uses option-level pricing if available, otherwise falls back to product pricing
    """
    if rental_type == 'buy':
        # âœ… UPGRADED: Prioritize buy_offer_price > buy_price > offer_price > price
        buy_offer = product_option.get_buy_offer_price()
        if buy_offer and buy_offer > 0:
            return buy_offer  # Best price - custom buy offer

        buy_price = product_option.get_buy_price()
        if buy_price > 0:
            return buy_price  # Custom buy price

        # Fallback to regular pricing
        offer_price = product_option.get_offer_price()
        if offer_price > 0:
            return offer_price

        return product_option.get_price()

    # Get rental price from ProductOption (includes auto-calculation fallback)
    return product_option.get_rental_price(rental_duration)


def _calculate_rental_price(product, rental_type, rental_duration):
    """
    Ã¢Å“â€¦ UPGRADED: Calculate rental price using database values
    Prioritizes offer prices for better customer experience
    """
    if rental_type == 'buy':
        # Ã¢Å“â€¦ UPGRADED: Prioritize buy_offer_price > buy_price > offer_price > price
        buy_offer = product.get_buy_offer_price()
        if buy_offer and buy_offer > 0:
            return buy_offer  # Best price - custom buy offer

        buy_price = product.get_buy_price()
        if buy_price > 0 and buy_price != product.price:
            return buy_price  # Custom buy price

        # Fallback to regular pricing
        return product.offer_price if product.offer_price > 0 else product.price

    # Get rental price from database (includes auto-calculation fallback)
    return product.get_rental_price(rental_duration)

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_product_booked_dates(request, product_id):
    """
    Get all booked dates for a specific product
    Query params: days (default 60) - number of days to look ahead
    """
    days = int(request.GET.get('days', 60))

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

    if not product.requires_date_selection:
        return Response({
            'success': True,
            'requires_date_selection': False,
            'booked_dates': []
        })

    today = timezone.now().date()
    end_date = today + timedelta(days=days)

    # Get all bookings
    bookings = ProductBooking.objects.filter(
        product=product,
        booking_date__gte=today,
        booking_date__lte=end_date,
        status__in=['PENDING', 'CONFIRMED']
    ).values('booking_date').annotate(
        total_booked=Sum('quantity_booked')
    ).order_by('booking_date')

    booked_dates = []
    for booking in bookings:
        max_per_date = product.max_bookings_per_date if product.max_bookings_per_date > 0 else 999999
        available = max_per_date - booking['total_booked']

        booked_dates.append({
            'date': booking['booking_date'].strftime('%Y-%m-%d'),
            'available_quantity': max(0, available),
            'is_fully_booked': available <= 0,
            'total_booked': booking['total_booked']
        })

    return Response({
        'success': True,
        'requires_date_selection': True,
        'max_bookings_per_date': product.max_bookings_per_date,
        'booked_dates': booked_dates
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def create_order_with_bookings(request):
    """
    ✅ COMPLETE: Enhanced order creation with rental information and date bookings
    ✅ UPGRADED: Decreases stock ONLY for purchases, NOT for rentals
    - RENT: Stock remains same (item will be returned)
    - BUY: Stock decreases (item is sold)
    - Stores user's selected date as expected_delivery
    - Handles both rental and purchase items
    - Creates ProductBooking entries for date-based items
    - Validates stock and date availability

    POST /api/orders/create/
    Body: {
        "cart_items": [
            {
                "product_option_id": "uuid",
                "quantity": 1,
                "selected_date": "2025-12-15",
                "rental_type": "rent",  // or "buy"
                "rental_duration": "3_days"
            }
        ],
        "payment_mode": "COD" or "ONLINE",
        "address": "Full delivery address",
        "tx_id": "transaction_id" (optional),
        "tx_status": "SUCCESS" (optional)
    }
    """
    from django.utils import timezone
    from datetime import timedelta

    user = request.user
    data = request.data

    # Validate required fields
    cart_items = data.get('cart_items', [])
    payment_mode = data.get('payment_mode')
    address = data.get('address')

    print("📦 Received cart items:")
    for item in cart_items:
        print(f"  - Product: {item.get('product_option_id')}")
        print(f"    Date: {item.get('selected_date')}")
        print(f"    Type: {item.get('rental_type')}")
        print(f"    Duration: {item.get('rental_duration')}")

    if not cart_items:
        return Response({
            'success': False,
            'message': 'Cart items are required'
        }, status=400)

    if not payment_mode or not address:
        return Response({
            'success': False,
            'message': 'Payment mode and address are required'
        }, status=400)

    # Terms & Conditions must be accepted to place order
    if not data.get('accepted_terms'):
        return Response({
            'success': False,
            'message': 'You must accept the Terms & Conditions to place an order'
        }, status=400)

    try:
        with transaction.atomic():
            # Calculate total amount and validate items
            total_amount = 0
            items_to_order = []
            bookings_to_create = []
            has_date_based_products = False
            earliest_delivery_date = None

            for item in cart_items:
                product_option_id = item.get('product_option_id')
                quantity = item.get('quantity', 1)
                selected_date = item.get('selected_date')
                rental_type = item.get('rental_type', 'buy')
                rental_duration = item.get('rental_duration')

                try:
                    product_option = ProductOption.objects.select_related(
                        'product',
                        'product__vendor'
                    ).get(id=product_option_id)
                except ProductOption.DoesNotExist:
                    return Response({
                        'success': False,
                        'message': f'Product option {product_option_id} not found'
                    }, status=404)

                product = product_option.product

                # Calculate rental dates
                rental_start_date = None
                rental_end_date = None

                if selected_date:
                    try:
                        rental_start_date = timezone.datetime.strptime(selected_date, '%Y-%m-%d').date()

                        if earliest_delivery_date is None or rental_start_date < earliest_delivery_date:
                            earliest_delivery_date = rental_start_date

                        if rental_type == 'rent' and rental_duration:
                            days_map = {
                                '1_day': 1, '2_days': 2, '3_days': 3,
                                '7_days': 7, '14_days': 14, '30_days': 30
                            }
                            days = days_map.get(rental_duration, 1)
                            rental_end_date = rental_start_date + timedelta(days=days - 1)

                    except ValueError:
                        return Response({
                            'success': False,
                            'message': f'Invalid date format for {product.title}'
                        }, status=400)

                # Check if product requires date and validate (only for rentals)
                if product.requires_date_selection and rental_type == 'rent':
                    has_date_based_products = True

                    if not selected_date:
                        return Response({
                            'success': False,
                            'message': f'{product.title} requires date selection for rental'
                        }, status=400)

                    if rental_start_date < timezone.now().date():
                        return Response({
                            'success': False,
                            'message': f'Cannot book past dates for {product.title}'
                        }, status=400)

                    # Check date availability
                    total_booked = ProductBooking.objects.filter(
                        product=product,
                        booking_date=rental_start_date,
                        status__in=['PENDING', 'CONFIRMED']
                    ).aggregate(total=Sum('quantity_booked'))['total'] or 0

                    max_per_date = product.max_bookings_per_date if product.max_bookings_per_date > 0 else 999999
                    available = max_per_date - total_booked

                    if available < quantity:
                        return Response({
                            'success': False,
                            'message': f'Only {available} slots available for {product.title} on {selected_date}'
                        }, status=400)

                    bookings_to_create.append({
                        'product': product,
                        'product_option': product_option,
                        'booking_date': rental_start_date,
                        'quantity': quantity,
                        'rental_type': rental_type,
                        'rental_duration': rental_duration,
                        'rental_end_date': rental_end_date
                    })

                # ✅ CRITICAL: Check stock availability
                if product_option.quantity < quantity:
                    return Response({
                        'success': False,
                        'message': f'Insufficient stock for {product.title}. Only {product_option.quantity} available.'
                    }, status=400)

                # Get price from CartItem or calculate
                rental_price = None

                try:
                    cart_item = CartItem.objects.filter(
                        user=user,
                        product_option=product_option,
                        rental_type=rental_type,
                        rental_duration=rental_duration if rental_type == 'rent' else ''
                    ).first()

                    if cart_item:
                        rental_price = cart_item.rental_price
                        print(f"✅ Using stored cart price: ₹{rental_price}")
                except Exception as e:
                    print(f"⚠️ Error getting cart price: {e}")

                if rental_price is None or rental_price == 0:
                    print(f"💡 Calculating rental price for {rental_type} - {rental_duration}")
                    rental_price = _calculate_rental_price_from_option(
                        product_option,
                        rental_type,
                        rental_duration
                    )
                    print(f"💰 Calculated price: ₹{rental_price}")

                delivery_charge = product.delivery_charge
                item_total = (rental_price * quantity) + delivery_charge
                total_amount += item_total

                item_security = int(getattr(product, 'security_amount', 0) or 0) * quantity

                print(
                    f"📊 Item total: ₹{item_total} (price: ₹{rental_price} x {quantity} + delivery: ₹{delivery_charge})")

                items_to_order.append({
                    'product_option': product_option,
                    'quantity': quantity,
                    'product_price': product.price,
                    'tx_price': rental_price,
                    'delivery_price': delivery_charge,
                    'security_amount': item_security,
                    'selected_date': selected_date,
                    'rental_type': rental_type,
                    'rental_duration': rental_duration,
                    'rental_start_date': rental_start_date,
                    'rental_end_date': rental_end_date
                })

            total_security = sum(item.get('security_amount', 0) for item in items_to_order)
            print(f"💵 Total order amount: ₹{total_amount}, Security: ₹{total_security}")

            # Coupon: validate and apply discount (re-validate at order creation for security)
            coupon_code = (data.get('coupon_code') or '').strip().upper()
            coupon_obj = None
            discount_amount = 0
            if coupon_code:
                from backend.utils import validate_coupon_and_calculate_discount
                product_option_ids_order = [str(item['product_option'].id) for item in items_to_order]
                success, msg, discount_amount, final_total, coupon_obj = validate_coupon_and_calculate_discount(
                    coupon_code=coupon_code,
                    user=user,
                    cart_total=total_amount,
                    product_option_ids=product_option_ids_order,
                )
                if not success:
                    return Response({
                        'success': False,
                        'message': msg or 'Invalid or expired coupon code',
                    }, status=400)
                total_amount = final_total
                print(f"🎟️ Coupon applied: {coupon_code}, discount ₹{discount_amount}, final ₹{total_amount}")

            # Trial-at-home upsell discount (after coupon, before wallet)
            trial_booking_id = data.get('trial_booking_id')
            trial_discount_applied = 0
            trial_obj = None
            if trial_booking_id:
                from backend.models import TrialSettings, TrialBooking
                trial_settings = TrialSettings.get_active()
                if not trial_settings or not bool(trial_settings.trial_discount_enabled):
                    return Response({
                        'success': False,
                        'message': 'Trial discount is currently disabled',
                    }, status=400)

                try:
                    trial_obj = TrialBooking.objects.select_for_update().prefetch_related('items').get(id=trial_booking_id)
                except TrialBooking.DoesNotExist:
                    return Response({
                        'success': False,
                        'message': 'Trial booking not found',
                    }, status=404)

                if trial_obj.user_id != user.id:
                    return Response({
                        'success': False,
                        'message': 'Trial booking does not belong to this user',
                    }, status=403)

                if trial_obj.payment_status != TrialBooking.PAYMENT_PAID:
                    return Response({
                        'success': False,
                        'message': 'Trial booking is not paid',
                    }, status=400)

                if trial_obj.status == TrialBooking.STATUS_CANCELLED:
                    return Response({
                        'success': False,
                        'message': 'Trial booking is cancelled and cannot be converted',
                    }, status=400)

                if trial_obj.converted_order_id:
                    return Response({
                        'success': False,
                        'message': 'This trial booking has already been used',
                    }, status=400)

                trial_option_ids = set(str(it.dress_id) for it in trial_obj.items.all())
                ordered_option_ids = set(str(item['product_option'].id) for item in items_to_order)

                # Must order only dresses from the trial booking (prevents "different dress" abuse)
                if not ordered_option_ids:
                    return Response({'success': False, 'message': 'No order items found'}, status=400)

                if not ordered_option_ids.issubset(trial_option_ids):
                    return Response({
                        'success': False,
                        'message': 'Trial discount only applies to dresses selected in the trial booking',
                    }, status=400)

                trial_fee_int = int(trial_obj.trial_fee or 0)
                if trial_fee_int > 0:
                    trial_discount_applied = min(trial_fee_int, int(total_amount))
                    total_amount = int(total_amount) - int(trial_discount_applied)
                    discount_amount = int(discount_amount) + int(trial_discount_applied)

            # Set expected_delivery
            if earliest_delivery_date:
                expected_delivery_str = earliest_delivery_date.strftime("%d %b %Y")
                print(f"📅 Expected delivery set to selected date: {expected_delivery_str}")
            else:
                expected_delivery_str = (timezone.now() + timedelta(days=2)).strftime("%d %b %Y")
                print(f"📅 Expected delivery calculated: {expected_delivery_str}")

            # Create Order
            order = Order.objects.create(
                user=user,
                tx_amount=total_amount,
                payment_mode=payment_mode,
                address=address,
                tx_id=data.get('tx_id', ''),
                tx_status='PENDING' if has_date_based_products else data.get('tx_status', 'INITIATED'),
                tx_time=timezone.now().strftime("%d %b %Y %H:%M %p"),
                tx_msg=data.get('tx_msg',
                                'Order placed, awaiting vendor confirmation' if has_date_based_products else ''),
                from_cart=data.get('from_cart', True),
                latitude=data.get('latitude'),
                longitude=data.get('longitude'),
                expected_delivery=expected_delivery_str,
                coupon=coupon_obj,
                discount_amount=discount_amount,
                trial_booking=trial_obj,
                security_amount=total_security,
                accepted_terms=True,
                accepted_at=timezone.now(),
            )

            print(f"✅ Order created: {order.id}")

            # Mark trial as converted (one-time use)
            if trial_obj:
                trial_obj.converted_order = order
                trial_obj.converted_at = timezone.now()
                trial_obj.save(update_fields=['converted_order', 'converted_at', 'updated_at'])

            # Apply referral wallet (after coupon)
            settings_obj = ReferralSettings.get_active()
            max_wallet_percent = settings_obj.max_wallet_usage_percent if settings_obj else 20
            requested_wallet_amount = int(data.get('wallet_amount') or 0)
            wallet_balance = int(user.referral_wallet_balance or 0)
            max_wallet_from_percent = int(total_amount * max_wallet_percent / 100) if max_wallet_percent > 0 else 0
            wallet_to_use = max(0, min(wallet_balance, requested_wallet_amount, max_wallet_from_percent, int(total_amount)))

            if wallet_to_use > 0:
                # Deduct from user wallet
                new_balance = wallet_balance - wallet_to_use
                user.referral_wallet_balance = new_balance
                user.save(update_fields=['referral_wallet_balance'])

                # Update order amount
                order.tx_amount = int(total_amount) - wallet_to_use
                order.save(update_fields=['tx_amount'])

                # Ledger entry
                WalletTransaction.objects.create(
                    user=user,
                    amount=wallet_to_use,
                    type=WalletTransaction.TYPE_DEBIT,
                    description="Used in order",
                    order=order,
                )

            # Record coupon usage and increment used_count
            if coupon_obj:
                from backend.models import CouponUsage
                CouponUsage.objects.create(user=user, coupon=coupon_obj, order=order)
                coupon_obj.used_count += 1
                coupon_obj.save(update_fields=['used_count', 'updated_at'])
                print(f"🎟️ Coupon usage recorded: {coupon_obj.code} (used {coupon_obj.used_count} times)")

            # ✅ UPGRADED: Create OrderedProduct entries AND decrease stock ONLY for purchases
            ordered_products = []
            for item in items_to_order:
                ordered_product = OrderedProduct.objects.create(
                    order=order,
                    product_option=item['product_option'],
                    quantity=item['quantity'],
                    product_price=item['product_price'],
                    tx_price=item['tx_price'],
                    delivery_price=item['delivery_price'],
                    rental_type=item['rental_type'],
                    rental_duration=item['rental_duration'],
                    rental_start_date=item['rental_start_date'],
                    rental_end_date=item['rental_end_date'],
                    status='ORDERED'
                )
                ordered_products.append(ordered_product)

                # ✅ CRITICAL: Decrease stock ONLY for purchases, NOT for rentals
                product_option = item['product_option']
                old_quantity = product_option.quantity

                if item['rental_type'] == 'buy':
                    # BUY: Decrease stock (item is sold)
                    product_option.quantity -= item['quantity']
                    product_option.save()

                    print(f"  ✅ OrderedProduct created: {product_option.product.title}")
                    print(f"  🛒 BUY - Stock decreased: {old_quantity} → {product_option.quantity}")
                else:
                    # RENT: Don't decrease stock (item will be returned)
                    print(f"  ✅ OrderedProduct created: {product_option.product.title}")
                    print(f"  🔄 RENT - Stock unchanged: {old_quantity} (item will be returned)")

            # Create ProductBooking entries (PENDING status)
            for booking_data in bookings_to_create:
                ProductBooking.objects.create(
                    product=booking_data['product'],
                    product_option=booking_data['product_option'],
                    booking_date=booking_data['booking_date'],
                    user=user,
                    order=order,
                    quantity_booked=booking_data['quantity'],
                    rental_type=booking_data['rental_type'],
                    rental_duration=booking_data['rental_duration'],
                    rental_end_date=booking_data['rental_end_date'],
                    status='PENDING'
                )

                print(f"  📅 ProductBooking created for {booking_data['booking_date']}")

            # Clear cart
            if data.get('from_cart', True):
                user.cart.clear()
                CartItem.objects.filter(user=user).delete()
                print(f"🗑️ Cart cleared")

            # Send notification
            try:
                Notification.objects.create(
                    user=user,
                    title='Order Placed Successfully' if not has_date_based_products else 'Order Awaiting Confirmation',
                    body=f'Your order #{str(order.id)[:8].upper()} has been placed. ' + (
                        'Waiting for vendor confirmation.' if has_date_based_products
                        else 'You will receive updates soon.'
                    ),
                    image=None
                )
                print(f"🔔 Notification sent")
            except Exception as e:
                print(f"Failed to create notification: {e}")

            # Send vendor push notification for newly booked products
            # (targets only the vendor(s) that own items in this order)
            try:
                from collections import defaultdict
                from backend.fcm_utils import send_fcm_to_vendor

                vendor_products_map = defaultdict(list)
                for ordered_product in ordered_products:
                    product = ordered_product.product_option.product
                    vendor = getattr(product, 'vendor', None)
                    if vendor:
                        vendor_products_map[vendor].append(product.title)
                        continue

                    # Fallback: resolve vendor from VendorProduct mapping
                    mapped_vendor_ids = VendorProduct.objects.filter(
                        product=product
                    ).values_list('vendor_id', flat=True)
                    if not mapped_vendor_ids:
                        continue
                    mapped_vendors = Vendor.objects.filter(id__in=mapped_vendor_ids)
                    for mapped_vendor in mapped_vendors:
                        vendor_products_map[mapped_vendor].append(product.title)

                for vendor, product_titles in vendor_products_map.items():
                    unique_titles = list(dict.fromkeys(product_titles))
                    titles_preview = ', '.join(unique_titles[:2])
                    if len(unique_titles) > 2:
                        titles_preview = f"{titles_preview} +{len(unique_titles) - 2} more"

                    send_fcm_to_vendor(
                        vendor,
                        'New booking received',
                        f'Order {str(order.id)[:8].upper()} includes: {titles_preview}',
                        data={
                            'type': 'vendor_new_booking',
                            'screen': 'orders',
                            'orderId': str(order.id),
                            'vendorId': str(vendor.id),
                        },
                    )
            except Exception as e:
                print(f"Failed to send vendor push notification: {e}")

            # Prepare response
            order_data = {
                'id': str(order.id),
                'order_number': f"RCO{str(order.id)[:8].upper()}",
                'total_amount': order.tx_amount,
                'discount_amount': order.discount_amount,
                'coupon_code': order.coupon.code if order.coupon else None,
                'trial_booking_id': str(order.trial_booking_id) if order.trial_booking_id else None,
                'trial_discount_applied': int(trial_discount_applied or 0),
                'payment_mode': order.payment_mode,
                'status': order.tx_status,
                'requires_confirmation': has_date_based_products,
                'expected_delivery': expected_delivery_str,
                'confirmation_message': (
                    'Your order is awaiting vendor confirmation. You will be notified once confirmed.'
                    if has_date_based_products
                    else 'Your order has been placed successfully.'
                ),
                'created_at': order.created_at.isoformat(),
                'items_count': len(ordered_products),
                'rental_summary': [
                    {
                        'product': item['product_option'].product.title,
                        'type': item['rental_type'],
                        'duration': item['rental_duration'] if item['rental_type'] == 'rent' else None,
                        'start_date': item['rental_start_date'].strftime('%Y-%m-%d') if item[
                            'rental_start_date'] else None,
                        'end_date': item['rental_end_date'].strftime('%Y-%m-%d') if item['rental_end_date'] else None,
                        'dates': f"{item['rental_start_date']} to {item['rental_end_date']}" if item[
                            'rental_start_date'] else None,
                        'price': item['tx_price'],
                        'quantity': item['quantity']
                    }
                    for item in items_to_order
                ]
            }

            print(f"✅ Order response prepared")

            return Response({
                'success': True,
                'message': 'Order placed successfully' + (
                    ' - Awaiting vendor confirmation' if has_date_based_products else ''
                ),
                'order': order_data
            }, status=201)

    except Exception as e:
        print(f"❌ Order creation failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to create order: {str(e)}'
        }, status=500)


# âœ… NEW: Helper function that uses ProductOption pricing
def _calculate_rental_price_from_option(product_option, rental_type, rental_duration):
    """
    Calculate rental price using ProductOption methods (includes auto-calculation)
    This ensures consistency with cart pricing

    Args:
        product_option: ProductOption instance
        rental_type: 'rent' or 'buy'
        rental_duration: '1_day', '2_days', etc.

    Returns:
        int: Rental price
    """
    if rental_type == 'buy':
        # Prioritize buy_offer_price > buy_price > offer_price > price
        buy_offer = product_option.get_buy_offer_price()
        if buy_offer and buy_offer > 0:
            return buy_offer

        buy_price = product_option.get_buy_price()
        if buy_price > 0:
            return buy_price

        # Fallback to regular pricing
        offer_price = product_option.get_offer_price()
        if offer_price > 0:
            return offer_price

        return product_option.get_price()

    # Get rental price from ProductOption (includes auto-calculation fallback)
    return product_option.get_rental_price(rental_duration)


# =============================================================================
# TRIAL AT HOME (Trial Booking + Upsell Discount)
# =============================================================================


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_trial_settings(request):
    """
    Returns active trial-at-home settings for the app UI.
    """
    from backend.models import TrialSettings

    settings_obj = TrialSettings.get_active()
    if not settings_obj:
        return Response({
            'success': True,
            'settings': {
                'trial_enabled_areas': [],
                'trial_fee': 0,
                'max_trial_items': 0,
                'trial_discount_enabled': False,
                'trial_slots': [],
            }
        })

    return Response({
        'success': True,
        'settings': {
            # Trial availability is controlled per vendor, not by area/location.
            'trial_enabled_areas': [],
            'trial_fee': int(settings_obj.trial_fee or 0),
            'max_trial_items': int(settings_obj.max_trial_items or 0),
            'trial_discount_enabled': bool(settings_obj.trial_discount_enabled),
            'trial_slots': list(settings_obj.trial_slots or []),
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def create_trial_booking(request):
    """
    Create a new trial booking.
    Body:
    {
      "address": "...",
      "area": "Area name",
      "trial_date": "YYYY-MM-DD",
      "time_slot": "morning",
      "dress_ids": ["product_option_uuid", ...]
    }
    """
    from django.utils import timezone
    from backend.models import TrialSettings, TrialBooking, TrialItem, ProductOption

    user = request.user
    data = request.data or {}

    settings_obj = TrialSettings.get_active()
    if not settings_obj:
        return Response({'success': False, 'message': 'Trial feature is not configured'}, status=400)

    slots = list(settings_obj.trial_slots or [])
    max_items = int(settings_obj.max_trial_items or 0)
    fee = int(settings_obj.trial_fee or 0)

    address = (data.get('address') or '').strip()
    area = (data.get('area') or '').strip()
    time_slot = (data.get('time_slot') or '').strip()
    trial_date_str = (data.get('trial_date') or '').strip()
    dress_ids = data.get('dress_ids') or data.get('product_option_ids') or []

    if not address or not area or not time_slot or not trial_date_str:
        return Response({'success': False, 'message': 'address, area, trial_date and time_slot are required'}, status=400)

    if not isinstance(dress_ids, list) or not dress_ids:
        return Response({'success': False, 'message': 'dress_ids must be a non-empty array'}, status=400)

    if slots and time_slot not in slots:
        return Response({'success': False, 'message': 'Invalid trial time slot'}, status=400)

    if max_items and len(dress_ids) > max_items:
        return Response({'success': False, 'message': f'Maximum {max_items} trial items allowed'}, status=400)

    try:
        trial_date = timezone.datetime.strptime(trial_date_str, '%Y-%m-%d').date()
    except ValueError:
        return Response({'success': False, 'message': 'Invalid trial_date format (YYYY-MM-DD)'}, status=400)

    if trial_date < timezone.now().date():
        return Response({'success': False, 'message': 'trial_date cannot be in the past'}, status=400)

    # Validate dresses exist (ProductOption)
    unique_ids = list(dict.fromkeys([str(x) for x in dress_ids]))
    options = list(ProductOption.objects.filter(id__in=unique_ids).select_related('product', 'product__vendor'))
    if len(options) != len(unique_ids):
        return Response({'success': False, 'message': 'One or more dresses not found'}, status=404)

    # Trial is controlled per vendor:
    # - All selected items must belong to trial-enabled vendor(s)
    # - For vendor workflow, enforce SINGLE vendor per trial booking
    ineligible = []
    vendor_ids = set()
    chosen_vendor = None
    for opt in options:
        vendor = getattr(getattr(opt, 'product', None), 'vendor', None)
        if vendor is None:
            ineligible.append(str(opt.id))
            continue
        if not bool(getattr(vendor, 'trial_enabled', False)):
            ineligible.append(str(opt.id))
        vendor_ids.add(vendor.id)
        chosen_vendor = vendor

    if ineligible:
        return Response(
            {
                'success': False,
                'message': 'Trial is not available for one or more selected dresses (vendor trial disabled)',
                'ineligible_dress_ids': ineligible,
            },
            status=400,
        )

    if len(vendor_ids) != 1:
        return Response(
            {
                'success': False,
                'message': 'Please select trial dresses from only one vendor at a time',
            },
            status=400,
        )

    with transaction.atomic():
        trial = TrialBooking.objects.create(
            user=user,
            vendor=chosen_vendor,
            address=address,
            area=area,
            trial_fee=fee,
            payment_status=TrialBooking.PAYMENT_UNPAID,
            status=TrialBooking.STATUS_PENDING,
            trial_date=trial_date,
            time_slot=time_slot,
        )
        TrialItem.objects.bulk_create([
            TrialItem(trial=trial, dress_id=opt.id) for opt in options
        ])

    # Notify vendor (best-effort) so vendor app can buzz immediately.
    try:
        if trial.vendor_id:
            from backend.fcm_utils import send_fcm_to_vendor
            title = 'New trial booking'
            body = f'New trial request for {trial.trial_date.strftime("%d %b")} {trial.time_slot}. Tap to view.'
            send_fcm_to_vendor(
                trial.vendor,
                title,
                body,
                data={'screen': 'vendor_trial', 'type': 'trial_new', 'trial_id': str(trial.id)},
            )
    except Exception as e:
        logger.warning('Trial vendor push failed (create): %s', e)

    return Response({
        'success': True,
        'message': 'Trial booking created',
        'trial_booking': {
            'id': str(trial.id),
            'trial_fee': int(trial.trial_fee or 0),
            'payment_status': trial.payment_status,
            'status': trial.status,
            'trial_date': trial.trial_date.strftime('%Y-%m-%d'),
            'time_slot': trial.time_slot,
            'area': trial.area,
            'items_count': len(options),
        }
    }, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def list_my_trial_bookings(request):
    from backend.models import TrialBooking

    user = request.user
    qs = TrialBooking.objects.filter(user=user).order_by('-created_at').prefetch_related('items')
    data = []
    for t in qs:
        data.append({
            'id': str(t.id),
            'area': t.area,
            'address': t.address,
            'trial_fee': int(t.trial_fee or 0),
            'payment_status': t.payment_status,
            'status': t.status,
            'trial_date': t.trial_date.strftime('%Y-%m-%d'),
            'time_slot': t.time_slot,
            'converted_order_id': str(t.converted_order_id) if t.converted_order_id else None,
            'items_count': t.items.count(),
            'created_at': t.created_at.isoformat(),
        })

    return Response({'success': True, 'trial_bookings': data, 'total': len(data)})


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def trial_booking_detail(request, trial_id):
    from backend.models import TrialBooking

    user = request.user
    try:
        trial = TrialBooking.objects.prefetch_related('items__dress__product').get(id=trial_id)
    except TrialBooking.DoesNotExist:
        return Response({'success': False, 'message': 'Trial booking not found'}, status=404)

    if trial.user_id != user.id:
        return Response({'success': False, 'message': 'Forbidden'}, status=403)

    items = []
    for it in trial.items.all():
        opt = it.dress
        prod = getattr(opt, 'product', None)
        items.append({
            'dress_id': str(opt.id),
            'title': str(opt),
            'product_id': str(prod.id) if prod else None,
        })

    return Response({
        'success': True,
        'trial_booking': {
            'id': str(trial.id),
            'area': trial.area,
            'address': trial.address,
            'trial_fee': int(trial.trial_fee or 0),
            'payment_status': trial.payment_status,
            'status': trial.status,
            'trial_date': trial.trial_date.strftime('%Y-%m-%d'),
            'time_slot': trial.time_slot,
            'converted_order_id': str(trial.converted_order_id) if trial.converted_order_id else None,
            'items': items,
            'created_at': trial.created_at.isoformat(),
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def mark_trial_booking_paid(request, trial_id):
    """
    Marks a trial booking as PAID (called after payment success).
    """
    from backend.models import TrialBooking

    user = request.user
    try:
        with transaction.atomic():
            trial = TrialBooking.objects.select_for_update().select_related('vendor').get(id=trial_id)
            if trial.user_id != user.id:
                return Response({'success': False, 'message': 'Forbidden'}, status=403)

            if trial.payment_status == TrialBooking.PAYMENT_PAID:
                return Response({'success': True, 'message': 'Already paid', 'payment_status': trial.payment_status})

            trial.payment_status = TrialBooking.PAYMENT_PAID
            trial.save(update_fields=['payment_status', 'updated_at'])
    except TrialBooking.DoesNotExist:
        return Response({'success': False, 'message': 'Trial booking not found'}, status=404)

    # Notify vendor (best-effort)
    try:
        if trial.vendor_id:
            from backend.fcm_utils import send_fcm_to_vendor
            title = 'New trial booking'
            body = f'New trial request for {trial.trial_date.strftime("%d %b")} {trial.time_slot}. Tap to view.'
            send_fcm_to_vendor(
                trial.vendor,
                title,
                body,
                data={'screen': 'vendor_trial', 'type': 'trial_new', 'trial_id': str(trial.id)},
            )
    except Exception as e:
        logger.warning('Trial vendor push failed: %s', e)

    return Response({'success': True, 'message': 'Payment marked as paid', 'payment_status': trial.payment_status})


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def referral_info(request):
    """
    Returns current user's referral summary:
    - referral_code
    - wallet_balance
    - total_earnings (sum of credits)
    - total_referrals (all referrals made)
    """
    user = request.user
    total_credits = WalletTransaction.objects.filter(
        user=user,
        type=WalletTransaction.TYPE_CREDIT
    ).aggregate(total=Sum('amount'))['total'] or 0

    total_referrals = Referral.objects.filter(referrer=user).count()

    # Ensure user always has a referral_code (generate if missing)
    if not user.referral_code:
        base_name = (user.fullname or user.email or '').strip().upper().replace(' ', '') or f"USER{user.phone[-4:]}"
        base_name = base_name[:8]
        from random import randint
        for _ in range(10):
            code = f"{base_name}{randint(1000, 9999)}"
            if not User.objects.filter(referral_code=code).exists():
                user.referral_code = code
                user.save(update_fields=['referral_code'])
                break

    settings_obj = ReferralSettings.get_active()
    max_wallet_usage_percent = int(settings_obj.max_wallet_usage_percent) if settings_obj else 20
    reward_per_friend = int(settings_obj.referral_reward_amount) if settings_obj and settings_obj.referral_reward_amount else 100

    return Response({
        'success': True,
        'referral_code': user.referral_code,
        'wallet_balance': str(user.referral_wallet_balance or 0),
        'total_earnings': str(total_credits),
        'total_referrals': total_referrals,
        'max_wallet_usage_percent': max_wallet_usage_percent,
        'reward_per_friend': reward_per_friend,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def referral_history(request):
    """
    Returns list of referrals made by the current user.
    Each item: friend_name, status, reward_amount, created_at, is_suspicious.
    """
    user = request.user
    referrals = Referral.objects.filter(referrer=user).select_related('referred_user').order_by('-created_at')

    history = []
    for r in referrals:
        friend_name = r.referred_user.fullname or r.referred_user.email or r.referred_user.phone
        history.append({
            'id': r.id,
            'friend_name': friend_name,
            'status': r.status,
            'reward_amount': str(r.reward_amount),
            'is_suspicious': r.is_suspicious,
            'fraud_reason': r.fraud_reason,
            'created_at': r.created_at.isoformat(),
        })

    return Response({
        'success': True,
        'referrals': history,
        'total': len(history),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def wallet_transactions(request):
    """
    Returns referral wallet transaction history for current user.
    Supports simple pagination via ?page=&page_size=
    """
    user = request.user
    try:
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 50))
    except ValueError:
        page, page_size = 1, 50

    qs = WalletTransaction.objects.filter(user=user).order_by('-created_at')
    paginator = Paginator(qs, page_size)
    try:
        page_obj = paginator.page(page)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    items = []
    for tx in page_obj.object_list:
        items.append({
            'id': tx.id,
            'amount': str(tx.amount),
            'type': tx.type,
            'description': tx.description,
            'created_at': tx.created_at.isoformat(),
            'order_id': str(tx.order_id) if tx.order_id else None,
            'service_booking_id': str(tx.service_booking_id) if tx.service_booking_id else None,
        })

    return Response({
        'success': True,
        'wallet_balance': str(user.referral_wallet_balance or 0),
        'transactions': items,
        'pagination': {
            'current_page': page_obj.number,
            'total_pages': paginator.num_pages,
            'total_items': paginator.count,
            'page_size': page_size,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def referral_share(request):
    """
    Generate a referral link for the current user.
    """
    user = request.user

    # Ensure referral code exists
    if not user.referral_code:
        base_name = (user.fullname or user.email or '').strip().upper().replace(' ', '') or f"USER{user.phone[-4:]}"
        base_name = base_name[:8]
        from random import randint
        for _ in range(10):
            code = f"{base_name}{randint(1000, 9999)}"
            if not User.objects.filter(referral_code=code).exists():
                user.referral_code = code
                user.save(update_fields=['referral_code'])
                break

    base_url = getattr(settings, 'REFERRAL_SIGNUP_BASE_URL', 'https://yourapp.com/signup')
    # Append ref param: use & if base_url already has query params (e.g. Play Store), else ?
    separator = "&" if "?" in base_url else "?"
    referral_link = f"{base_url}{separator}ref={user.referral_code}"

    share_message = (
        f"Try our Rental Clothes app! Use my code {user.referral_code} "
        f"and earn rewards on your first order. Sign up here: {referral_link}"
    )

    return Response({
        'success': True,
        'referral_code': user.referral_code,
        'referral_link': referral_link,
        'share_message': share_message,
    })

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_order_confirmation_status(request, order_id):
    """
    Check if an order has been confirmed by vendor
    """
    user = request.user

    try:
        order = Order.objects.get(id=order_id, user=user)
    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Order not found'
        }, status=404)

    # Get booking statuses
    bookings = ProductBooking.objects.filter(order=order)

    booking_statuses = []
    all_confirmed = True
    any_cancelled = False

    for booking in bookings:
        booking_statuses.append({
            'product': booking.product.title,
            'date': booking.booking_date.strftime('%Y-%m-%d'),
            'status': booking.status,
            'quantity': booking.quantity_booked
        })

        if booking.status != 'CONFIRMED':
            all_confirmed = False
        if booking.status == 'CANCELLED':
            any_cancelled = True

    return Response({
        'success': True,
        'order_id': str(order.id),
        'order_number': f"BH{str(order.id)[:8].upper()}",
        'order_status': order.tx_status,
        'all_confirmed': all_confirmed,
        'any_cancelled': any_cancelled,
        'bookings': booking_statuses,
        'confirmation_message': (
            'All bookings confirmed!' if all_confirmed
            else 'Some bookings cancelled' if any_cancelled
            else 'Awaiting vendor confirmation'
        )
    })


class VendorAuthentication:
    """Custom authentication for vendors"""

    @staticmethod
    def authenticate(request):
        token = request.headers.get('Authorization')
        if not token:
            return None

        try:
            vendor_token = VendorToken.objects.select_related('vendor').get(token=token)

            # Check if token is expired
            if vendor_token.expires_at and vendor_token.expires_at < timezone.now():
                return None

            if not vendor_token.vendor.is_active:
                return None

            return vendor_token.vendor
        except VendorToken.DoesNotExist:
            return None


def vendor_required(view_func):
    """Decorator to require vendor authentication"""

    def wrapper(request, *args, **kwargs):
        vendor = VendorAuthentication.authenticate(request)
        if not vendor:
            return Response({
                'success': False,
                'message': 'Authentication required'
            }, status=status.HTTP_401_UNAUTHORIZED)

        request.vendor = vendor
        return view_func(request, *args, **kwargs)

    return wrapper


@api_view(['POST'])
def vendor_login(request):
    """
    Vendor login - Token stays valid until logout
    POST /api/vendor/login/
    Body: {"email": "vendor@example.com", "password": "password"}
    """
    email = request.data.get('email', '').strip()
    password = request.data.get('password', '')
    fcm_token = (request.data.get('fcmtoken') or request.data.get('fcm_token') or '').strip()

    if not email or not password:
        return Response({
            'success': False,
            'message': 'Email and password are required'
        }, status=400)

    try:
        vendor = Vendor.objects.get(email=email)

        if not vendor.is_active:
            return Response({
                'success': False,
                'message': 'Account is deactivated. Contact administrator.'
            }, status=403)

        if check_password(password, vendor.password):
            # Generate new token
            token = uuid.uuid4().hex

            # Create token (stays valid until logout)
            VendorToken.objects.create(
                token=token,
                vendor=vendor,
                fcmtoken=fcm_token
            )

            return Response({
                'success': True,
                'message': 'Login successful',
                'token': token,
                'vendor': {
                    'id': str(vendor.id),
                    'vendor_id': vendor.vendor_id,
                    'name': vendor.name,
                    'email': vendor.email,
                    'phone': vendor.phone,
                }
            })
        else:
            return Response({
                'success': False,
                'message': 'Invalid credentials'
            }, status=401)

    except Vendor.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Invalid credentials'
        }, status=401)
    except Exception as e:
        print(f"Login error: {str(e)}")
        return Response({
            'success': False,
            'message': f'Login failed: {str(e)}'
        }, status=500)


@api_view(['POST'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_logout(request):
    """
    Logout vendor - Deletes the token
    POST /api/vendor/logout/
    """
    token = request.headers.get('Authorization')

    if token:
        try:
            # Extract token value
            token_parts = str(token).split()
            if len(token_parts) == 2:
                token_value = token_parts[1]

                # Delete the token
                VendorToken.objects.filter(token=token_value).delete()

                return Response({
                    'success': True,
                    'message': 'Logged out successfully'
                })
        except Exception as e:
            print(f"Logout error: {str(e)}")

    return Response({
        'success': False,
        'message': 'Logout failed'
    }, status=400)


@api_view(['POST'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_save_device_token(request):
    """
    Save/update FCM token for current authenticated vendor session.
    POST /api/vendor/save-device-token/
    Body: {"fcm_token": "..."} or {"fcmtoken": "..."}
    """
    fcm_token = request.data.get('fcm_token') or request.data.get('fcmtoken')
    if not fcm_token or not str(fcm_token).strip():
        return Response({
            'success': False,
            'message': 'fcm_token is required'
        }, status=400)

    token_header = request.headers.get('Authorization', '')
    token_parts = str(token_header).split()
    if len(token_parts) != 2:
        return Response({
            'success': False,
            'message': 'Invalid Authorization header'
        }, status=400)

    token_value = token_parts[1]
    updated = VendorToken.objects.filter(
        token=token_value,
        vendor=request.user
    ).update(fcmtoken=str(fcm_token).strip())

    if not updated:
        return Response({
            'success': False,
            'message': 'Vendor token not found'
        }, status=404)

    return Response({
        'success': True,
        'message': 'Vendor device token saved'
    })



@api_view(['GET'])
@vendor_required
def vendor_orders(request):
    """
    Get vendor's orders with filtering
    GET /api/vendor/orders/?status=PENDING&page=1
    """
    vendor = request.vendor

    # Get vendor's products
    vendor_product_ids = VendorProduct.objects.filter(
        vendor=vendor
    ).values_list('product_id', flat=True)

    # Get orders containing vendor's products
    orders_queryset = Order.objects.filter(
        orders_set__product_option__product_id__in=vendor_product_ids
    ).distinct().select_related('user').prefetch_related(
        'orders_set__product_option__product',
        'orders_set__product_option__images_set'
    ).order_by('-created_at')

    # Filtering
    status_filter = request.GET.get('status')
    if status_filter:
        orders_queryset = orders_queryset.filter(vendor_status=status_filter)

    search_query = request.GET.get('search')
    if search_query:
        orders_queryset = orders_queryset.filter(
            Q(id__icontains=search_query) |
            Q(user__fullname__icontains=search_query) |
            Q(user__email__icontains=search_query) |
            Q(user__phone__icontains=search_query)
        )

    # Pagination
    page = int(request.GET.get('page', 1))
    page_size = 20
    start = (page - 1) * page_size
    end = start + page_size

    total_count = orders_queryset.count()
    total_pages = (total_count + page_size - 1) // page_size

    orders = orders_queryset[start:end]

    # Serialize
    orders_data = []
    for order in orders:
        # Get only vendor's products from this order
        vendor_items = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        )

        orders_data.append({
            'id': str(order.id),
            'user': {
                'fullname': order.user.fullname,
                'email': order.user.email,
                'phone': order.user.phone,
            },
            'items': VendorOrderDetailSerializer(vendor_items, many=True).data,
            'tx_amount': order.tx_amount,
            'payment_mode': order.payment_mode,
            'tx_status': order.tx_status,
            'vendor_status': order.vendor_status,
            'address': order.address,
            'vendor_notes': order.vendor_notes,
            'created_at': order.created_at.isoformat(),
            'vendor_accepted_at': order.vendor_accepted_at.isoformat() if order.vendor_accepted_at else None,
        })

    return Response({
        'success': True,
        'orders': orders_data,
        'pagination': {
            'current_page': page,
            'total_pages': total_pages,
            'total_count': total_count,
            'has_next': page < total_pages,
            'has_previous': page > 1,
        }
    })


@api_view(['GET'])
@vendor_required
def vendor_order_detail(request, order_id):
    """
    Get detailed order information
    GET /api/vendor/orders/<order_id>/
    """
    vendor = request.vendor

    # Get vendor's products
    vendor_product_ids = VendorProduct.objects.filter(
        vendor=vendor
    ).values_list('product_id', flat=True)

    try:
        order = Order.objects.select_related('user').prefetch_related(
            'orders_set__product_option__product',
            'orders_set__product_option__images_set'
        ).get(id=order_id)

        # Verify vendor has access to this order
        order_has_vendor_products = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        ).exists()

        if not order_has_vendor_products:
            return Response({
                'success': False,
                'message': 'Order not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)

        # Get only vendor's products from this order
        vendor_items = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        )

        return Response({
            'success': True,
            'order': {
                'id': str(order.id),
                'user': {
                    'fullname': order.user.fullname,
                    'email': order.user.email,
                    'phone': order.user.phone,
                },
                'items': VendorOrderDetailSerializer(vendor_items, many=True).data,
                'tx_amount': order.tx_amount,
                'payment_mode': order.payment_mode,
                'tx_status': order.tx_status,
                'tx_id': order.tx_id,
                'vendor_status': order.vendor_status,
                'address': order.address,
                'vendor_notes': order.vendor_notes,
                'latitude': float(order.latitude) if order.latitude else None,
                'longitude': float(order.longitude) if order.longitude else None,
                'created_at': order.created_at.isoformat(),
                'vendor_accepted_at': order.vendor_accepted_at.isoformat() if order.vendor_accepted_at else None,
                'vendor_rejected_at': order.vendor_rejected_at.isoformat() if order.vendor_rejected_at else None,
            }
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Order not found'
        }, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@vendor_required
def vendor_accept_order(request, order_id):
    """
    Accept an order
    POST /api/vendor/orders/<order_id>/accept/
    Body: {"notes": "Optional notes"}
    """
    vendor = request.vendor
    notes = request.data.get('notes', '')

    # Get vendor's products
    vendor_product_ids = VendorProduct.objects.filter(
        vendor=vendor
    ).values_list('product_id', flat=True)

    try:
        order = Order.objects.get(id=order_id)

        # Verify vendor has access
        order_has_vendor_products = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        ).exists()

        if not order_has_vendor_products:
            return Response({
                'success': False,
                'message': 'Order not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)

        # Check if already processed
        if order.vendor_status in ['ACCEPTED', 'REJECTED']:
            return Response({
                'success': False,
                'message': f'Order already {order.vendor_status.lower()}'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Accept order
        order.vendor_status = 'ACCEPTED'
        order.vendor_notes = notes
        order.vendor_accepted_at = timezone.now()
        order.assigned_vendor = vendor
        order.save()

        # Update ordered products status
        vendor_items = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        )
        vendor_items.update(status='ORDERED')

        # Send notification to customer (optional)
        try:
            from backend.utils import send_user_notification
            send_user_notification(
                order.user,
                "Order Accepted 🎉",
                f"Your order has been accepted and is being processed.",
                None
            )
        except Exception:
            pass
        try:
            _send_accept_push_notification(order.user, order.id)
        except Exception:
            pass

        return Response({
            'success': True,
            'message': 'Order accepted successfully',
            'order': {
                'id': str(order.id),
                'vendor_status': order.vendor_status,
                'vendor_accepted_at': order.vendor_accepted_at.isoformat(),
            }
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Order not found'
        }, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@vendor_required
def vendor_reject_order(request, order_id):
    """
    Reject an order
    POST /api/vendor/orders/<order_id>/reject/
    Body: {"reason": "Reason for rejection"}
    """
    vendor = request.vendor
    reason = request.data.get('reason', 'No reason provided')

    # Get vendor's products
    vendor_product_ids = VendorProduct.objects.filter(
        vendor=vendor
    ).values_list('product_id', flat=True)

    try:
        order = Order.objects.get(id=order_id)

        # Verify vendor has access
        order_has_vendor_products = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        ).exists()

        if not order_has_vendor_products:
            return Response({
                'success': False,
                'message': 'Order not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)

        # Check if already processed
        if order.vendor_status in ['ACCEPTED', 'REJECTED']:
            return Response({
                'success': False,
                'message': f'Order already {order.vendor_status.lower()}'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Reject order
        order.vendor_status = 'REJECTED'
        order.vendor_notes = reason
        order.vendor_rejected_at = timezone.now()
        order.save()

        # Restore stock for rejected items
        vendor_items = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        )

        for item in vendor_items:
            item.product_option.quantity += item.quantity
            item.product_option.save()
            item.status = 'CANCELLED'
            item.save()

        # Send notification to customer (optional)
        try:
            from backend.utils import send_user_notification
            send_user_notification(
                order.user,
                "Order Rejected Ã¢ÂÅ’",
                f"Your order has been rejected. Reason: {reason}",
                None
            )
        except Exception:
            pass
        try:
            _send_reject_push_notification(order.user, order.id)
        except Exception:
            pass

        return Response({
            'success': True,
            'message': 'Order rejected successfully',
            'order': {
                'id': str(order.id),
                'vendor_status': order.vendor_status,
                'vendor_rejected_at': order.vendor_rejected_at.isoformat(),
            }
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Order not found'
        }, status=status.HTTP_404_NOT_FOUND)


# ==================== PRODUCTS ====================

@api_view(['GET'])
@vendor_required
def vendor_products(request):
    """Get vendor's assigned products"""
    vendor = request.vendor

    vendor_product_ids = VendorProduct.objects.filter(
        vendor=vendor
    ).values_list('product_id', flat=True)

    products = Product.objects.filter(
        id__in=vendor_product_ids
    ).select_related('category').prefetch_related(
        'options_set__images_set'
    )

    # Filtering
    search = request.GET.get('search')
    if search:
        products = products.filter(
            Q(title__icontains=search) |
            Q(description__icontains=search)
        )

    category_id = request.GET.get('category')
    if category_id:
        products = products.filter(category_id=category_id)

    # Pagination
    page = int(request.GET.get('page', 1))
    page_size = 20
    start = (page - 1) * page_size
    end = start + page_size

    total_count = products.count()
    total_pages = (total_count + page_size - 1) // page_size

    products = products[start:end]

    # Serialize with enhanced data
    products_data = []
    for product in products:
        total_stock = product.options_set.aggregate(
            total=Sum('quantity')
        )['total'] or 0

        total_options = product.options_set.count()

        first_image = None
        first_option = product.options_set.first()
        if first_option and first_option.images_set.exists():
            first_image = request.build_absolute_uri(
                first_option.images_set.first().image.url
            )

        products_data.append({
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'category': {
                'id': product.category.id,
                'name': product.category.name,
            } if product.category else None,
            'total_stock': total_stock,
            'total_options': total_options,
            'image': first_image,
            'created_at': product.created_at.isoformat(),
            'options': ProductSerializer(product, context={'request': request}).data['options'],
        })

    return Response({
        'success': True,
        'products': products_data,
        'pagination': {
            'current_page': page,
            'total_pages': total_pages,
            'total_products': total_count,
            'has_next': page < total_pages,
            'has_previous': page > 1,
        }
    })


@api_view(['GET'])
@vendor_required
def vendor_product_detail(request, product_id):
    """Get detailed product information"""
    vendor = request.vendor

    # Verify vendor has access
    if not VendorProduct.objects.filter(vendor=vendor, product_id=product_id).exists():
        return Response({
            'success': False,
            'message': 'Product not found or access denied'
        }, status=status.HTTP_404_NOT_FOUND)

    try:
        product = Product.objects.select_related('category').prefetch_related(
            'options_set__images_set'
        ).get(id=product_id)

        serializer = ProductSerializer(product, context={'request': request})

        return Response({
            'success': True,
            'product': serializer.data
        })

    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@vendor_required
def vendor_categories(request):
    """Get all categories"""
    vendor = getattr(request, 'vendor', None) or getattr(request, 'user', None)
    pincodes = []
    try:
        if getattr(vendor, 'serviceable_locations', None) is not None:
            pincodes = list(
                vendor.serviceable_locations.filter(is_active=True).values_list('pincode', flat=True)
            )
    except Exception:
        pincodes = []

    if not pincodes:
        pincode = (getattr(vendor, 'pincode', '') or '').strip()
        if pincode:
            pincodes = [pincode]

    categories = Category.objects.all().order_by('position')
    if pincodes:
        categories = categories.filter(
            location_availability__location__pincode__in=pincodes,
            location_availability__location__is_active=True,
            location_availability__is_available=True,
        ).distinct().order_by('position')

    serializer = CategorySerializer(categories, many=True)

    return Response({
        'success': True,
        'categories': serializer.data
    })



@api_view(['GET'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_orders(request):
    """
    Get vendor's orders with filtering and pagination
    GET /api/vendor/orders/?status=PENDING&page=1&search=query
    """
    vendor = request.user  # This is the Vendor instance from authentication

    try:
        # Get vendor's products
        vendor_product_ids = Product.objects.filter(vendor=vendor).values_list('id', flat=True)

        # Get orders containing vendor's products
        orders_queryset = Order.objects.filter(
            orders_set__product_option__product_id__in=vendor_product_ids
        ).distinct().select_related('user').prefetch_related(
            'orders_set__product_option__product',
            'orders_set__product_option__images_set'
        ).order_by('-created_at')

        # Apply status filter
        status_filter = request.GET.get('status')
        if status_filter:
            orders_queryset = orders_queryset.filter(vendor_status=status_filter)

        # Apply search filter
        search_query = request.GET.get('search')
        if search_query:
            orders_queryset = orders_queryset.filter(
                Q(id__icontains=search_query) |
                Q(user__fullname__icontains=search_query) |
                Q(user__email__icontains=search_query) |
                Q(user__phone__icontains=search_query)
            )

        # Pagination
        page = int(request.GET.get('page', 1))
        page_size = 20
        start = (page - 1) * page_size
        end = start + page_size

        total_count = orders_queryset.count()
        total_pages = (total_count + page_size - 1) // page_size

        orders = orders_queryset[start:end]

        # Serialize orders
        orders_data = []
        for order in orders:
            # Get only vendor's products from this order
            vendor_items = order.orders_set.filter(
                product_option__product_id__in=vendor_product_ids
            )

            # Serialize items
            items_data = []
            for item in vendor_items:
                first_image = item.product_option.images_set.first()
                image_url = None
                if first_image and first_image.image:
                    try:
                        image_url = request.build_absolute_uri(first_image.image.url)
                    except:
                        pass

                items_data.append({
                    'id': str(item.id),
                    'product_title': item.product_option.product.title,
                    'product_option_name': item.product_option.option or 'Default',
                    'product_image': image_url,
                    'quantity': item.quantity,
                    'product_price': item.product_price,
                    'tx_price': item.tx_price,
                    'delivery_price': item.delivery_price,
                    'status': item.status,
                    'created_at': item.created_at.isoformat(),
                    'rental_type': item.rental_type,
                    'rental_duration': item.rental_duration,
                    'rental_start_date': item.rental_start_date.strftime('%Y-%m-%d') if item.rental_start_date else None,
                    'rental_end_date': item.rental_end_date.strftime('%Y-%m-%d') if item.rental_end_date else None,
                })

            orders_data.append({
                'id': str(order.id),
                'user': {
                    'fullname': order.user.fullname,
                    'email': order.user.email,
                    'phone': order.user.phone,
                },
                'items': items_data,
                'tx_amount': order.tx_amount,
                'payment_mode': order.payment_mode,
                'tx_status': order.tx_status,
                'vendor_status': order.vendor_status,
                'address': order.address,
                'vendor_notes': order.vendor_notes,
                'created_at': order.created_at.isoformat(),
                'vendor_accepted_at': order.vendor_accepted_at.isoformat() if order.vendor_accepted_at else None,
                'latitude': float(order.latitude) if order.latitude else None,
                'longitude': float(order.longitude) if order.longitude else None,
            })

        return Response({
            'success': True,
            'orders': orders_data,
            'pagination': {
                'current_page': page,
                'total_pages': total_pages,
                'total_count': total_count,
                'has_next': page < total_pages,
                'has_previous': page > 1,
            }
        })

    except Exception as e:
        print(f"Error in vendor_orders: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to load orders: {str(e)}'
        }, status=500)


@api_view(['GET'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_order_detail(request, order_id):
    """
    Get detailed order information
    GET /api/vendor/orders/<order_id>/
    """
    vendor = request.user

    try:
        # Get vendor's products
        vendor_product_ids = Product.objects.filter(vendor=vendor).values_list('id', flat=True)

        # Get the order
        order = Order.objects.select_related('user').prefetch_related(
            'orders_set__product_option__product',
            'orders_set__product_option__images_set'
        ).get(id=order_id)

        # Verify vendor has access to this order
        order_has_vendor_products = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        ).exists()

        if not order_has_vendor_products:
            return Response({
                'success': False,
                'message': 'Order not found or access denied'
            }, status=404)

        # Get only vendor's products from this order
        vendor_items = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        )

        # Serialize items
        items_data = []
        for item in vendor_items:
            first_image = item.product_option.images_set.first()
            image_url = None
            if first_image and first_image.image:
                try:
                    image_url = request.build_absolute_uri(first_image.image.url)
                except:
                    pass

            items_data.append({
                'id': str(item.id),
                'product_title': item.product_option.product.title,
                'product_option_name': item.product_option.option or 'Default',
                'product_image': image_url,
                'quantity': item.quantity,
                'product_price': item.product_price,
                'tx_price': item.tx_price,
                'delivery_price': item.delivery_price,
                'status': item.status,
                'created_at': item.created_at.isoformat(),
                'rental_type': item.rental_type,
                'rental_duration': item.rental_duration,
                'rental_start_date': item.rental_start_date.strftime('%Y-%m-%d') if item.rental_start_date else None,
                'rental_end_date': item.rental_end_date.strftime('%Y-%m-%d') if item.rental_end_date else None,
            })

        order_data = {
            'id': str(order.id),
            'user': {
                'fullname': order.user.fullname,
                'email': order.user.email,
                'phone': order.user.phone,
            },
            'items': items_data,
            'tx_amount': order.tx_amount,
            'payment_mode': order.payment_mode,
            'tx_status': order.tx_status,
            'tx_id': order.tx_id,
            'vendor_status': order.vendor_status,
            'address': order.address,
            'vendor_notes': order.vendor_notes,
            'latitude': float(order.latitude) if order.latitude else None,
            'longitude': float(order.longitude) if order.longitude else None,
            'created_at': order.created_at.isoformat(),
            'vendor_accepted_at': order.vendor_accepted_at.isoformat() if order.vendor_accepted_at else None,
            'vendor_rejected_at': order.vendor_rejected_at.isoformat() if order.vendor_rejected_at else None,
        }

        return Response({
            'success': True,
            'order': order_data
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Order not found'
        }, status=404)
    except Exception as e:
        print(f"Error in vendor_order_detail: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to load order: {str(e)}'
        }, status=500)


@api_view(['POST'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_accept_order(request, order_id):
    """
    Accept an order
    POST /api/vendor/orders/<order_id>/accept/
    Body: {"notes": "Optional notes"}
    """
    print(f'📲 Vendor accept order: order_id={order_id} (push will be sent to customer)')
    vendor = request.user
    notes = request.data.get('notes', '')

    try:
        # Get vendor's products
        vendor_product_ids = Product.objects.filter(vendor=vendor).values_list('id', flat=True)

        # Get the order
        order = Order.objects.get(id=order_id)

        # Verify vendor has access
        order_has_vendor_products = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        ).exists()

        if not order_has_vendor_products:
            return Response({
                'success': False,
                'message': 'Order not found or access denied'
            }, status=404)

        # Check if already processed
        if order.vendor_status in ['ACCEPTED', 'REJECTED']:
            return Response({
                'success': False,
                'message': f'Order already {order.vendor_status.lower()}'
            }, status=400)

        # Accept order
        order.vendor_status = 'ACCEPTED'
        order.vendor_notes = notes
        order.vendor_accepted_at = timezone.now()
        order.assigned_vendor = vendor
        order.save()

        # Update ordered products status
        vendor_items = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        )
        vendor_items.update(status='ORDERED')

        # Update product bookings to CONFIRMED
        from backend.models import ProductBooking
        ProductBooking.objects.filter(
            order=order,
            product_id__in=vendor_product_ids,
            status='PENDING'
        ).update(status='CONFIRMED')

        # Send notification to customer (in-app)
        try:
            Notification.objects.create(
                user=order.user,
                title="Order Accepted 🎉",
                body=f"Your order #{str(order.id)[:8].upper()} has been accepted by {vendor.name} and is being processed.",
                image=None
            )
        except Exception as e:
            print(f"Failed to send notification: {e}")

        # Push notification to customer (FCM)
        try:
            _send_accept_push_notification(order.user, order.id)
        except Exception as e:
            print(f"Push notification (accept): {e}")

        return Response({
            'success': True,
            'message': 'Order accepted successfully',
            'order': {
                'id': str(order.id),
                'vendor_status': order.vendor_status,
                'vendor_accepted_at': order.vendor_accepted_at.isoformat(),
            }
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Order not found'
        }, status=404)
    except Exception as e:
        print(f"Error in vendor_accept_order: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to accept order: {str(e)}'
        }, status=500)


@api_view(['POST'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_reject_order(request, order_id):
    """
    Reject an order
    POST /api/vendor/orders/<order_id>/reject/
    Body: {"reason": "Reason for rejection"}
    """
    vendor = request.user
    reason = request.data.get('reason', 'No reason provided')

    if not reason or reason.strip() == '':
        return Response({
            'success': False,
            'message': 'Rejection reason is required'
        }, status=400)

    try:
        # Get vendor's products
        vendor_product_ids = Product.objects.filter(vendor=vendor).values_list('id', flat=True)

        # Get the order
        order = Order.objects.get(id=order_id)

        # Verify vendor has access
        order_has_vendor_products = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        ).exists()

        if not order_has_vendor_products:
            return Response({
                'success': False,
                'message': 'Order not found or access denied'
            }, status=404)

        # Check if already processed
        if order.vendor_status in ['ACCEPTED', 'REJECTED']:
            return Response({
                'success': False,
                'message': f'Order already {order.vendor_status.lower()}'
            }, status=400)

        # Reject order
        order.vendor_status = 'REJECTED'
        order.vendor_notes = f"Rejection Reason: {reason}"
        order.vendor_rejected_at = timezone.now()
        order.save()

        # Restore stock for rejected items
        vendor_items = order.orders_set.filter(
            product_option__product_id__in=vendor_product_ids
        )

        for item in vendor_items:
            item.product_option.quantity += item.quantity
            item.product_option.save()
            item.status = 'CANCELLED'
            item.save()

        # Cancel product bookings
        from backend.models import ProductBooking
        ProductBooking.objects.filter(
            order=order,
            product_id__in=vendor_product_ids,
            status='PENDING'
        ).update(status='CANCELLED')

        # Send notification to customer
        try:
            Notification.objects.create(
                user=order.user,
                title="Order Rejected Ã¢ÂÅ’",
                body=f"Your order #{str(order.id)[:8].upper()} has been rejected. Reason: {reason}",
                image=None
            )
        except Exception as e:
            print(f"Failed to send notification: {e}")

        # Push notification (FCM) to customer
        try:
            _send_reject_push_notification(order.user, order.id)
        except Exception as e:
            print(f"Push notification (reject): {e}")

        return Response({
            'success': True,
            'message': 'Order rejected successfully',
            'order': {
                'id': str(order.id),
                'vendor_status': order.vendor_status,
                'vendor_rejected_at': order.vendor_rejected_at.isoformat(),
            }
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Order not found'
        }, status=404)
    except Exception as e:
        print(f"Error in vendor_reject_order: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to reject order: {str(e)}'
        }, status=500)


# Update the vendor_dashboard function to include pending and accepted orders
@api_view(['GET'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_dashboard(request):
    """
    Get vendor dashboard statistics - Updated with order counts
    """
    vendor = request.user

    try:
        # Get all products for this vendor
        products = Product.objects.filter(vendor=vendor)

        # Calculate statistics
        total_products = products.count()
        total_options = ProductOption.objects.filter(product__in=products).count()
        total_stock = ProductOption.objects.filter(product__in=products).aggregate(
            total=Sum('quantity')
        )['total'] or 0

        # Get low stock items (less than 10)
        low_stock_items = ProductOption.objects.filter(
            product__in=products,
            quantity__lt=10,
            quantity__gt=0
        ).count()

        # Get vendor's product IDs
        vendor_product_ids = products.values_list('id', flat=True)

        # Get recent orders (last 30 days)
        from django.utils import timezone
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        recent_orders = Order.objects.filter(
            orders_set__product_option__product_id__in=vendor_product_ids,
            created_at__gte=thirty_days_ago
        ).distinct().count()

        # Get pending orders (NEW)
        pending_orders = Order.objects.filter(
            orders_set__product_option__product_id__in=vendor_product_ids,
            vendor_status='PENDING'
        ).distinct().count()

        # Get accepted orders (NEW)
        accepted_orders = Order.objects.filter(
            orders_set__product_option__product_id__in=vendor_product_ids,
            vendor_status='ACCEPTED'
        ).distinct().count()

        # Calculate total revenue (successful orders)
        total_revenue = Order.objects.filter(
            orders_set__product_option__product_id__in=vendor_product_ids,
            tx_status='SUCCESS'
        ).distinct().aggregate(total=Sum('tx_amount'))['total'] or 0

        return Response({
            'success': True,
            'dashboard': {
                'vendor_info': {
                    'vendor_id': vendor.vendor_id,
                    'name': vendor.name,
                    'email': vendor.email,
                },
                'total_products': total_products,
                'total_options': total_options,
                'total_stock': total_stock,
                'low_stock_items': low_stock_items,
                'recent_orders': recent_orders,
                'pending_orders': pending_orders,
                'accepted_orders': accepted_orders,
                'total_revenue': int(total_revenue)
            }
        })
    except Exception as e:
        print(f"Error in vendor_dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to load dashboard: {str(e)}'
        }, status=500)


# =============================================================================
# VENDOR: TRIAL BOOKINGS (Vendor accepts/rejects)
# =============================================================================

@api_view(['GET'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_trial_bookings(request):
    """
    Vendor trial requests list.
    Query params:
      - decision: pending|accepted|rejected (default: pending)
    """
    vendor = request.user
    decision = (request.GET.get('decision') or 'pending').strip().lower()
    if decision not in ['pending', 'accepted', 'rejected']:
        decision = 'pending'

    from backend.models import TrialBooking

    qs = TrialBooking.objects.filter(
        vendor=vendor,
        vendor_decision=decision,
    ).select_related('user').prefetch_related('items__dress__product')

    data = []
    for t in qs.order_by('-created_at')[:200]:
        items = []
        for it in t.items.all():
            opt = it.dress
            prod = getattr(opt, 'product', None)
            items.append({
                'product_option_id': str(opt.id),
                'title': str(opt),
                'product_id': str(prod.id) if prod else None,
                'product_title': getattr(prod, 'title', None),
            })

        data.append({
            'id': str(t.id),
            'trial_date': t.trial_date.strftime('%Y-%m-%d'),
            'time_slot': t.time_slot,
            'address': t.address,
            'area': t.area,
            'trial_fee': int(t.trial_fee or 0),
            'payment_status': t.payment_status,
            'status': t.status,
            'vendor_decision': t.vendor_decision,
            'vendor_message': t.vendor_message or '',
            'created_at': t.created_at.isoformat(),
            'customer': {
                'id': t.user_id,
                'fullname': t.user.fullname,
                'phone': t.user.phone,
                'email': t.user.email,
            },
            'items': items,
            'items_count': len(items),
        })

    pending_count = TrialBooking.objects.filter(vendor=vendor, vendor_decision='pending').count()

    return Response({
        'success': True,
        'trial_bookings': data,
        'pending_count': pending_count,
        'decision': decision,
        'total': len(data),
    })


@api_view(['POST'])
@authentication_classes([VendorTokenAuthentication])
@permission_classes([IsAuthenticatedVendor])
def vendor_trial_decide(request, trial_id):
    """
    Vendor accepts/rejects a trial booking.
    Body:
      - decision: accepted|rejected
      - message: optional text
    """
    vendor = request.user
    decision = (request.data.get('decision') or '').strip().lower()
    message = (request.data.get('message') or '').strip()

    if decision not in ['accepted', 'rejected']:
        return Response({'success': False, 'message': 'Invalid decision'}, status=400)

    from backend.models import TrialBooking
    try:
        with transaction.atomic():
            trial = TrialBooking.objects.select_for_update().select_related('user').get(id=trial_id, vendor=vendor)

            if trial.vendor_decision != TrialBooking.DECISION_PENDING:
                return Response({
                    'success': False,
                    'message': f'Trial already {trial.vendor_decision}',
                }, status=400)

            trial.vendor_decision = decision
            trial.vendor_message = message
            trial.vendor_decided_at = timezone.now()

            # Map vendor decision to fulfillment status for existing UI
            if decision == 'accepted':
                trial.status = TrialBooking.STATUS_APPROVED
            else:
                trial.status = TrialBooking.STATUS_CANCELLED

            trial.save(update_fields=['vendor_decision', 'vendor_message', 'vendor_decided_at', 'status', 'updated_at'])

    except TrialBooking.DoesNotExist:
        return Response({'success': False, 'message': 'Trial booking not found'}, status=404)

    # Notify customer
    customer = trial.user
    if decision == 'accepted':
        title = 'Trial request accepted'
        body = (
            f'Your trial request is accepted by vendor. '
            f'Please be available on {trial.trial_date.strftime("%d %b %Y")} at {trial.time_slot} '
            f'at your address.'
        )
    else:
        title = 'Trial request rejected'
        body = 'Your trial request was rejected by vendor.'
        if message:
            body = f'{body} Reason: {message}'

    _notify_user_trial_decision(
        customer,
        title,
        body,
        data={'screen': 'trial', 'type': 'trial_vendor_decision', 'trial_id': str(trial.id), 'decision': decision},
    )

    return Response({'success': True, 'message': f'Trial {decision}', 'trial_id': str(trial.id), 'decision': decision})


# =============================================================================
# SERVICE VENDOR (Services mode) - OTP auth + Service management
# =============================================================================

@api_view(['POST'])
@permission_classes([AllowAny])
def service_vendor_request_otp(request):
    phone = (request.data.get('phone') or '').strip()
    if not phone:
        return Response({'success': False, 'message': 'phone is required'}, status=400)
    return send_otp(phone)


@api_view(['POST'])
@permission_classes([AllowAny])
def service_vendor_verify_otp(request):
    phone = (request.data.get('phone') or '').strip()
    otp = request.data.get('otp')
    if not phone or not otp:
        return Response({'success': False, 'message': 'phone and otp are required'}, status=400)

    otp_obj = get_object_or_404(Otp, phone=phone, verified=False)
    if otp_obj.validity.replace(tzinfo=None) <= datetime.datetime.utcnow():
        return Response({'success': False, 'message': 'otp expired'}, status=400)

    try:
        if otp_obj.otp != int(otp):
            return Response({'success': False, 'message': 'Incorrect otp'}, status=400)
    except Exception:
        return Response({'success': False, 'message': 'Invalid otp'}, status=400)

    otp_obj.verified = True
    otp_obj.save()
    return Response({'success': True, 'message': 'otp_verified_successfully'})


@api_view(['POST'])
@permission_classes([AllowAny])
def service_vendor_signup(request):
    """
    Create (or log in) a ServiceVendor after OTP verification.
    """
    name = (request.data.get('name') or '').strip()
    area = (request.data.get('area') or '').strip()
    pincode = (request.data.get('pincode') or '').strip()
    phone = (request.data.get('phone') or '').strip()
    otp = request.data.get('otp')
    fcmtoken = (request.data.get('fcmtoken') or request.data.get('fcm_token') or '').strip()
    subcategory_ids = request.data.get('service_subcategory_ids') or request.data.get('service_subcategories') or []

    if not all([name, phone, otp]):
        return Response({'success': False, 'message': 'name, phone and otp are required'}, status=400)

    verified_otp = Otp.objects.filter(phone=phone, verified=True).order_by('-id').first()
    if not verified_otp:
        return Response({'success': False, 'message': 'OTP not verified'}, status=400)

    vendor, _created = ServiceVendor.objects.get_or_create(
        phone=phone,
        defaults={'name': name, 'area': area, 'pincode': pincode},
    )

    if not vendor.is_active:
        return Response({'success': False, 'message': 'Account is deactivated. Contact administrator.'}, status=403)

    # Update basic profile fields
    updates = {}
    if name and vendor.name != name:
        updates['name'] = name
    if area and vendor.area != area:
        updates['area'] = area
    if pincode and vendor.pincode != pincode:
        updates['pincode'] = pincode
    if updates:
        ServiceVendor.objects.filter(pk=vendor.pk).update(**updates)
        vendor.refresh_from_db()

    # Attach allowed subcategories
    if isinstance(subcategory_ids, str):
        subcategory_ids = [s.strip() for s in subcategory_ids.split(',') if s.strip()]
    if isinstance(subcategory_ids, list) and subcategory_ids:
        qs = ServiceSubCategory.objects.filter(id__in=subcategory_ids)
        vendor.service_subcategories.set(qs)

    token = uuid.uuid4().hex
    ServiceVendorToken.objects.create(token=token, vendor=vendor, fcmtoken=fcmtoken)

    return Response({
        'success': True,
        'message': 'Signup successful',
        'token': token,
        'vendor': {
            'id': str(vendor.id),
            'service_vendor_id': vendor.service_vendor_id,
            'name': vendor.name,
            'phone': vendor.phone,
            'area': vendor.area,
            'pincode': vendor.pincode,
            'service_subcategories': list(vendor.service_subcategories.values('id', 'name')),
        }
    })


@api_view(['POST'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_logout(request):
    token = request.headers.get('Authorization')
    if token:
        try:
            token_parts = str(token).split()
            if len(token_parts) == 2:
                token_value = token_parts[1]
                ServiceVendorToken.objects.filter(token=token_value).delete()
                return Response({'success': True, 'message': 'Logged out successfully'})
        except Exception:
            pass
    return Response({'success': False, 'message': 'Logout failed'}, status=400)


@api_view(['GET'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_my_services(request):
    vendor = request.user
    services = Service.objects.filter(service_vendor=vendor).order_by('-created_at')
    data = ServiceSerializer(services, many=True, context={'request': request}).data
    return Response({'success': True, 'services': data})


@api_view(['POST'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_create_service(request):
    vendor = request.user

    title = (request.data.get('title') or '').strip()
    description = (request.data.get('description') or '').strip()
    base_price = int(request.data.get('base_price') or 0)
    category_id = request.data.get('category_id')
    subcategory_id = request.data.get('subcategory_id')
    location = (request.data.get('location') or vendor.area or '').strip()
    languages = (request.data.get('languages') or 'Hindi, English').strip()

    if not title or not description:
        return Response({'success': False, 'message': 'title and description are required'}, status=400)

    category = ServiceCategory.objects.filter(id=category_id).first() if category_id else None
    subcategory = ServiceSubCategory.objects.filter(id=subcategory_id).first() if subcategory_id else None

    service = Service.objects.create(
        title=title,
        description=description,
        base_price=base_price,
        category=category or (subcategory.category if subcategory else None),
        subcategory=subcategory,
        location=location,
        provider_name=vendor.name,
        provider_phone=vendor.phone,
        provider_email='',
        languages=languages,
        service_vendor=vendor,
    )

    return Response({
        'success': True,
        'message': 'Service created',
        'service': ServiceSerializer(service, context={'request': request}).data
    }, status=201)


@api_view(['PUT'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_update_service(request, service_id):
    vendor = request.user
    service = get_object_or_404(Service, id=service_id, service_vendor=vendor)

    for field in ['title', 'description', 'location', 'languages']:
        if field in request.data and request.data.get(field) is not None:
            setattr(service, field, str(request.data.get(field)).strip())

    if 'base_price' in request.data:
        try:
            service.base_price = int(request.data.get('base_price') or 0)
        except Exception:
            return Response({'success': False, 'message': 'Invalid base_price'}, status=400)

    if 'subcategory_id' in request.data:
        subcategory_id = request.data.get('subcategory_id')
        service.subcategory = ServiceSubCategory.objects.filter(id=subcategory_id).first() if subcategory_id else None

    if 'category_id' in request.data:
        category_id = request.data.get('category_id')
        service.category = ServiceCategory.objects.filter(id=category_id).first() if category_id else None

    service.provider_name = vendor.name
    service.provider_phone = vendor.phone
    service.save()

    return Response({
        'success': True,
        'message': 'Service updated',
        'service': ServiceSerializer(service, context={'request': request}).data
    })


@api_view(['DELETE'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_delete_service(request, service_id):
    vendor = request.user
    service = get_object_or_404(Service, id=service_id, service_vendor=vendor)
    service.delete()
    return Response({'success': True, 'message': 'Service deleted'})


@api_view(['GET'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_dashboard(request):
    """
    Basic dashboard stats for service vendors.
    """
    vendor = request.user

    services_qs = Service.objects.filter(service_vendor=vendor)
    bookings_qs = ServiceBooking.objects.filter(service_option__service__service_vendor=vendor)

    total_services = services_qs.count()
    total_bookings = bookings_qs.count()
    pending_bookings = bookings_qs.filter(status='PENDING').count()
    confirmed_bookings = bookings_qs.filter(status='CONFIRMED').count()

    total_revenue = bookings_qs.filter(payment_status='PAID').aggregate(total=Sum('total_amount'))['total'] or 0

    return Response({
        'success': True,
        'dashboard': {
            'vendor_info': {
                'service_vendor_id': vendor.service_vendor_id,
                'name': vendor.name,
                'phone': vendor.phone,
                'area': vendor.area,
                'pincode': vendor.pincode,
            },
            'total_services': total_services,
            'total_bookings': total_bookings,
            'pending_bookings': pending_bookings,
            'confirmed_bookings': confirmed_bookings,
            'total_revenue': int(total_revenue),
        }
    })


@api_view(['GET'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_bookings(request):
    """
    List bookings for a service vendor.
    GET /api/service-vendor/bookings/?status=PENDING&page=1&search=...
    """
    vendor = request.user

    bookings = ServiceBooking.objects.filter(
        service_option__service__service_vendor=vendor
    ).select_related(
        'service_option',
        'service_option__service',
        'user',
    ).order_by('-created_at')

    status_filter = (request.GET.get('status') or '').strip().upper()
    if status_filter and status_filter != 'ALL':
        bookings = bookings.filter(status=status_filter)

    search = (request.GET.get('search') or '').strip()
    if search:
        bookings = bookings.filter(
            Q(customer_name__icontains=search) |
            Q(customer_phone__icontains=search) |
            Q(customer_address__icontains=search) |
            Q(id__icontains=search) |
            Q(service_option__service__title__icontains=search) |
            Q(service_option__option_name__icontains=search)
        )

    page = int(request.GET.get('page', 1))
    page_size = 20
    start = (page - 1) * page_size
    end = start + page_size

    total_count = bookings.count()
    total_pages = (total_count + page_size - 1) // page_size

    page_items = bookings[start:end]
    data = ServiceBookingSerializer(page_items, many=True, context={'request': request}).data

    return Response({
        'success': True,
        'bookings': data,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total_count': total_count,
            'total_pages': total_pages,
            'has_next': page < total_pages,
        }
    })


@api_view(['POST'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_confirm_booking(request, booking_id):
    vendor = request.user
    booking = get_object_or_404(
        ServiceBooking,
        id=booking_id,
        service_option__service__service_vendor=vendor
    )
    booking.status = 'CONFIRMED'
    booking.save(update_fields=['status', 'updated_at'])
    return Response({'success': True, 'message': 'Booking confirmed'})


@api_view(['POST'])
@authentication_classes([ServiceVendorTokenAuthentication])
@permission_classes([IsAuthenticatedServiceVendor])
def service_vendor_cancel_booking(request, booking_id):
    vendor = request.user
    booking = get_object_or_404(
        ServiceBooking,
        id=booking_id,
        service_option__service__service_vendor=vendor
    )
    booking.status = 'CANCELLED'
    booking.save(update_fields=['status', 'updated_at'])
    return Response({'success': True, 'message': 'Booking cancelled'})


@api_view(['GET'])
@permission_classes([AllowAny])
def get_serviceable_locations(request):
    """
    Get all serviceable locations
    GET /api/serviceable-locations/
    """
    try:
        locations = ServiceableLocation.objects.filter(is_active=True).order_by('city', 'area_name')

        locations_data = []
        for location in locations:
            locations_data.append({
                'pincode': location.pincode,
                'area_name': location.area_name,
                'city': location.city,
                'state': location.state,
                'rent_available': location.rent_available,
                'service_available': location.service_available,
                'delivery_charge': location.delivery_charge,
                'delivery_time': location.delivery_time,
            })

        return Response({
            'success': True,
            'locations': locations_data,
            'total_count': len(locations_data)
        })

    except Exception as e:
        print(f"Error fetching serviceable locations: {e}")
        return Response({
            'success': False,
            'message': 'Failed to fetch locations',
            'locations': []
        }, status=500)


# views.py - Update these endpoints to allow guest access

# ✅ FIXED: Service Categories - Allow guests
@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Changed from IsAuthenticatedUser
def get_service_categories(request):
    """
    Get service categories with serviceability check
    ✅ NOW WORKS FOR GUESTS
    """
    pincode = request.GET.get('pincode')

    # ✅ FIXED: Safely check authentication
    user = getattr(request, 'user', None)
    is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

    print(f'🔧 Fetching service categories for pincode: {pincode}')
    print(f'🎭 Guest Mode: {not is_authenticated}')

    # Check serviceability
    is_serviceable = True
    serviceability_message = "Service categories loaded"

    if pincode:
        from backend.utils import check_pincode_serviceability
        is_serviceable, location_info, serviceability_message = check_pincode_serviceability(pincode)

        if not is_serviceable:
            return Response({
                'is_serviceable': False,
                'message': serviceability_message,
                'categories': [],
                'total_categories': 0
            })

        if location_info and not location_info.service_available:
            return Response({
                'is_serviceable': True,
                'message': 'Services are not available in your location yet',
                'categories': [],
                'total_categories': 0
            })

    try:
        if pincode:
            from backend.utils import get_available_service_categories_for_pincode
            categories = get_available_service_categories_for_pincode(pincode)
        else:
            categories = ServiceCategory.objects.all().order_by('position')

        print(f'✅ Found {categories.count()} service categories')

        categories_data = ServiceCategorySerializer(
            categories,
            many=True,
            context={'request': request}
        ).data

        return Response({
            'is_serviceable': True,
            'categories': categories_data,
            'total_categories': len(categories_data)
        }, status=200)

    except Exception as e:
        print(f'❌ Error: {str(e)}')
        return Response({
            'success': False,
            'message': 'Unable to load service categories',
            'categories': [],
        }, status=500)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_service_subcategories(request, category_id):
    """
    Get sub-categories for a service category (e.g. Decoration -> Bridal Entry, Haldi, Mehendi, Sangeet).
    Returns ALL subcategories for the category - no location/availability filter.
    When a category is available in a location, all its subcategories are shown.
    """
    try:
        category = ServiceCategory.objects.get(pk=category_id)
        subcategories = ServiceSubCategory.objects.filter(category=category).order_by('position')
        data = ServiceSubCategorySerializer(
            subcategories,
            many=True,
            context={'request': request}
        ).data
        return Response({
            'success': True,
            'category_id': category_id,
            'category_name': category.name,
            'subcategories': data,
        }, status=200)
    except ServiceCategory.DoesNotExist:
        return Response({'success': False, 'message': 'Category not found'}, status=404)


# views.py - Complete get_all_services endpoint

@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Allow guest access
def get_all_services(request):
    """
    Get all available services with filtering and pagination
    ✅ NOW WORKS FOR GUESTS

    Query Parameters:
        - category: Filter by category ID
        - subcategory: Filter by subcategory ID (e.g. Bridal Entry, Haldi)
        - sort: Sorting option (relevance, price_low_high, price_high_low, rating, experience)
        - page: Page number for pagination
        - pincode: Filter by serviceable location

    Returns:
        {
            'success': True,
            'services': [...],
            'total_pages': 5,
            'current_page': 1,
            'total_services': 42,
            'has_next': True,
            'has_previous': False
        }
    """
    try:
        # Get query parameters
        category_id = request.GET.get('category', None)
        subcategory_id = request.GET.get('subcategory', None)
        sort_by = request.GET.get('sort', 'relevance')
        page = request.GET.get('page', 1)
        pincode = request.GET.get('pincode')

        # ✅ Check if guest mode
        user = getattr(request, 'user', None)
        is_authenticated = user is not None and hasattr(user, 'is_authenticated') and user.is_authenticated

        print(f'🔧 Fetching services:')
        print(f'   - Category: {category_id}')
        print(f'   - Subcategory: {subcategory_id}')
        print(f'   - Sort: {sort_by}')
        print(f'   - Page: {page}')
        print(f'   - Pincode: {pincode}')
        print(f'   - Guest Mode: {not is_authenticated}')

        # Base queryset - only available services
        services = Service.objects.select_related('category', 'subcategory').prefetch_related(
            'options_set__images_set'
        ).filter(availability=True)

        # Apply subcategory filter (takes precedence when set)
        if subcategory_id:
            try:
                subcategory_id_int = int(subcategory_id)
                services = services.filter(subcategory_id=subcategory_id_int)
                print(f'   ✅ Filtered by subcategory: {subcategory_id_int}')
            except (ValueError, TypeError):
                print(f'   ⚠️ Invalid subcategory ID: {subcategory_id}')
                pass
        # Apply category filter (services in this category, including via subcategories)
        elif category_id:
            try:
                category_id_int = int(category_id)
                from django.db.models import Q
                services = services.filter(Q(category_id=category_id_int) | Q(subcategory__category_id=category_id_int))
                print(f'   ✅ Filtered by category: {category_id_int}')
            except (ValueError, TypeError):
                print(f'   ⚠️ Invalid category ID: {category_id}')
                pass

        # Apply pincode filter (if you have location-based services)
        if pincode:
            # Optional: Filter services available in this pincode
            # You can add this logic based on your ServiceableLocation model
            pass

        # Apply sorting
        if sort_by == 'price_low_high':
            services = services.order_by('base_price')
            print(f'   ✅ Sorted by: Price Low to High')
        elif sort_by == 'price_high_low':
            services = services.order_by('-base_price')
            print(f'   ✅ Sorted by: Price High to Low')
        elif sort_by == 'rating':
            services = services.order_by('-rating', '-total_reviews')
            print(f'   ✅ Sorted by: Rating')
        elif sort_by == 'experience':
            # Assuming you have experience_years field
            services = services.order_by('-experience_years')
            print(f'   ✅ Sorted by: Experience')
        else:
            # Default: Most recent first
            services = services.order_by('-created_at')
            print(f'   ✅ Sorted by: Most Recent')

        # Pagination
        paginator = Paginator(services, 20)  # 20 services per page

        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        print(f'   ✅ Page {page_obj.number} of {paginator.num_pages}')
        print(f'   ✅ Total services: {paginator.count}')

        # Serialize services
        services_data = ServiceSerializer(
            page_obj,
            many=True,
            context={'request': request}
        ).data

        return Response({
            'success': True,
            'services': services_data,
            'total_pages': paginator.num_pages,
            'current_page': page_obj.number,
            'total_services': paginator.count,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
        }, status=200)

    except Exception as e:
        print(f'❌ Error fetching services: {str(e)}')
        import traceback
        traceback.print_exc()

        return Response({
            'success': False,
            'message': 'Unable to load services',
            'services': [],
            'error_detail': str(e) if settings.DEBUG else None
        }, status=500)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_home_page_item_products(request, home_page_item_id):
    """
    âœ… Get specific products from a home page item with their selected options
    This ensures View All shows exactly what was configured in admin

    GET /api/home-page-items/<home_page_item_id>/products/?page=1&sort=price_low_high
    """
    try:
        home_page_item = HomePageItem.objects.select_related('category').prefetch_related(
            'product_options__product__category',
            'product_options__images_set'
        ).get(id=home_page_item_id, is_active=True)

        print(f"ðŸ“¦ Fetching products for: {home_page_item.title}")

        sort_by = request.GET.get('sort', 'price_low_high')
        page = int(request.GET.get('page', 1))

        # Get the product options selected in admin
        items_queryset = list(home_page_item.product_options.select_related(
            'product__category'
        ).prefetch_related('images_set').all())

        print(f"âœ… Found {len(items_queryset)} product options")

        # Apply sorting (default: by product position within category)
        if sort_by == 'price_low_high':
            items_queryset.sort(key=lambda x: x.get_price_per_day('1_day') or 999999)
        elif sort_by == 'price_high_low':
            items_queryset.sort(key=lambda x: x.get_price_per_day('1_day') or 0, reverse=True)
        elif sort_by == 'newest':
            items_queryset.sort(key=lambda x: x.product.created_at, reverse=True)
        else:
            items_queryset.sort(key=lambda x: (
                getattr(x.product, 'position', 9999),
                -(x.product.created_at.timestamp() if x.product.created_at else 0)
            ))

        # Pagination
        paginator = Paginator(items_queryset, 20)

        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        # Build product data
        products_data = []
        for item in page_obj:
            product = item.product

            first_image = item.images_set.first()
            image_url = None
            if first_image and first_image.image:
                try:
                    image_url = request.build_absolute_uri(first_image.image.url)
                except:
                    pass

            # Build product with THIS specific option
            product_data = {
                'id': str(product.id),
                'title': product.title,
                'description': product.description,
                'price': product.price,
                'offer_price': product.offer_price,
                'delivery_charge': product.delivery_charge,
                'cod': product.cod,
                'position': getattr(product, 'position', 9999),
                'category_name': product.category.name if product.category else None,
                'created_at': product.created_at.isoformat(),
                'image': image_url,

                # âœ… Include ONLY the selected option
                'options': [ProductOptionSerializer(item, context={'request': request}).data]
            }

            products_data.append(product_data)

        return Response({
            'success': True,
            'products': products_data,
            'total_pages': paginator.num_pages,
            'current_page': page_obj.number,
            'total_products': paginator.count,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
        })

    except HomePageItem.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Home page item not found',
            'products': []
        }, status=404)
    except Exception as e:
        print(f"âŒ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to load products: {str(e)}',
            'products': []
        }, status=500)


@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Changed from IsAuthenticatedUser
def get_service_details(request, service_id):
    """
    Get detailed information about a specific service
    ✅ NOW WORKS FOR GUESTS
    """
    try:
        service = Service.objects.select_related('category').prefetch_related(
            'options_set__images_set'
        ).get(id=service_id)
    except Service.DoesNotExist:
        return Response({'error': 'Service not found'}, status=404)

    service_data = ServiceSerializer(service, context={'request': request}).data

    # Get related services from the same category
    related_services = Service.objects.filter(
        category=service.category,
        availability=True
    ).exclude(id=service.id)[:6]

    related_data = ServiceSerializer(
        related_services,
        many=True,
        context={'request': request}
    ).data

    return Response({
        'service': service_data,
        'related_services': related_data
    })


# ✅ FIXED: Service Page Items - Allow guests
@api_view(['GET'])
@permission_classes([AllowAny])  # ✅ Changed from IsAuthenticatedUser
def get_service_page_items(request):
    """
    Get service page items for home screen with appropriate limits
    ✅ NOW WORKS FOR GUESTS
    """
    try:
        category_id = request.GET.get('category', None)
        pincode = request.GET.get('pincode', None)

        page_items = ServicePageItem.objects.select_related('category').prefetch_related(
            'service_options__service',
            'service_options__images_set'
        ).order_by('category__position', 'position')

        if category_id:
            try:
                page_items = page_items.filter(category_id=int(category_id))
            except (ValueError, TypeError):
                pass

        page_items_data = []
        for page_item in page_items[:10]:
            # ✅ Apply limits based on viewtype
            if page_item.viewtype == 3:  # GRID
                limit = 4
            elif page_item.viewtype == 2:  # SWIPER
                limit = 8
            else:  # BANNER
                limit = 20

            options = page_item.service_options.all()[:limit]

            service_options_data = []
            for option in options:
                first_image = option.images_set.first()
                image_url = None
                if first_image:
                    request_obj = request
                    image_url = request_obj.build_absolute_uri(
                        first_image.image.url) if request_obj else first_image.image.url

                service_options_data.append({
                    'id': str(option.service.id),
                    'option_id': str(option.id),
                    'image': image_url,
                    'title': f"{option.option_name} - {option.service.title}",
                    'price': option.price,
                    'duration': option.duration,
                    'provider_name': option.service.provider_name,
                    'rating': float(option.service.rating),
                    'available': option.available,
                })

            page_items_data.append({
                'id': page_item.id,
                'position': page_item.position,
                'image': request.build_absolute_uri(page_item.image.url) if page_item.image and request else None,
                'category': page_item.category.id,
                'category_name': page_item.category.name,
                'title': page_item.title,
                'viewtype': page_item.viewtype,
                'service_options': service_options_data,
                'total_services': page_item.service_options.count(),  # ✅ Add total count
            })

        return Response({
            'success': True,
            'page_items': page_items_data,
            'total_items': len(page_items_data)
        }, status=200)

    except Exception as e:
        print(f'❌ Error fetching service page items: {str(e)}')
        return Response({
            'success': False,
            'message': 'Unable to load service items',
            'page_items': [],
            'error_detail': str(e) if settings.DEBUG else None
        }, status=500)

@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticatedUser])
def update_cart_item_quantity(request, item_id):
    """
    Update cart item quantity
    PUT/PATCH /api/cart/update/<item_id>/
    Body: {"quantity": 3}
    """
    user = request.user
    new_quantity = request.data.get('quantity')

    if new_quantity is None:
        return Response({
            'success': False,
            'message': 'Quantity is required'
        }, status=400)

    try:
        new_quantity = int(new_quantity)
        if new_quantity < 1:
            return Response({
                'success': False,
                'message': 'Quantity must be at least 1'
            }, status=400)
    except (ValueError, TypeError):
        return Response({
            'success': False,
            'message': 'Invalid quantity format'
        }, status=400)

    try:
        # Find the cart item
        cart_item = CartItem.objects.select_related(
            'product_option__product'
        ).get(id=item_id, user=user)

        # Check stock availability
        if new_quantity > cart_item.product_option.quantity:
            return Response({
                'success': False,
                'message': f'Only {cart_item.product_option.quantity} items available in stock'
            }, status=400)

        # Update quantity
        cart_item.quantity = new_quantity
        cart_item.save()

        print(f"âœ… Cart item {item_id} quantity updated to {new_quantity}")

        # Build updated cart response
        cart_items = CartItem.objects.select_related(
            'product_option__product'
        ).prefetch_related(
            'product_option__images_set'
        ).filter(user=user).order_by('-created_at')

        cart_data = []
        total_amount = 0
        offer_amount = 0
        total_savings = 0
        delivery_charges = 0

        for item in cart_items:
            product = item.product_option.product
            first_image = item.product_option.images_set.first()
            image_url = None
            if first_image and first_image.image:
                try:
                    image_url = request.build_absolute_uri(first_image.image.url)
                except:
                    pass

            original_price = product.price
            rental_price = item.rental_price
            savings_per_item = original_price - rental_price if rental_price < original_price else 0

            total_amount += original_price * item.quantity
            offer_amount += rental_price * item.quantity
            total_savings += savings_per_item * item.quantity
            delivery_charges += product.delivery_charge

            # Calculate rental end date
            rental_end_date = None
            if item.rental_type == 'rent' and item.selected_date:
                try:
                    from datetime import datetime, timedelta
                    start_date = datetime.strptime(str(item.selected_date), '%Y-%m-%d')
                    duration_days = {
                        '1_day': 1, '2_days': 2, '3_days': 3,
                        '7_days': 7, '14_days': 14, '30_days': 30
                    }
                    days = duration_days.get(item.rental_duration, 1)
                    end_date = start_date + timedelta(days=days - 1)
                    rental_end_date = end_date.strftime('%Y-%m-%d')
                except:
                    pass

            cart_data.append({
                'id': str(item.id),
                'product_option_id': str(item.product_option.id),
                'title': f"({item.product_option.option}) {product.title}" if item.product_option.option else product.title,
                'image': image_url,
                'price': original_price,
                'offer_price': product.offer_price if product.offer_price > 0 else original_price,
                'quantity': item.quantity,
                'cod': product.cod,
                'delivery_charge': product.delivery_charge,
                'savings': savings_per_item,
                'product_id': str(product.id),
                'in_stock': item.product_option.quantity > 0,
                'stock_quantity': item.product_option.quantity,
                'selected_date': item.selected_date.strftime('%Y-%m-%d') if item.selected_date else None,
                'rental_type': item.rental_type,
                'rental_duration': item.rental_duration,
                'rental_price': rental_price,
                'rental_end_date': rental_end_date,
            })

        final_amount = offer_amount + delivery_charges
        free_delivery_threshold = 500
        if offer_amount >= free_delivery_threshold:
            delivery_charges = 0
            final_amount = offer_amount

        return Response({
            'success': True,
            'message': 'Quantity updated successfully',
            'cart_items': cart_data,
            'summary': {
                'total_amount': total_amount,
                'offer_amount': offer_amount,
                'total_savings': total_savings,
                'delivery_charges': delivery_charges,
                'final_amount': final_amount,
                'total_items': len(cart_data),
                'free_delivery_threshold': free_delivery_threshold,
                'free_delivery_eligible': offer_amount >= free_delivery_threshold,
                'amount_for_free_delivery': max(0, free_delivery_threshold - offer_amount)
            },
            'recommendations': {
                'similar_products': [],
                'frequently_bought_together': []
            }
        })

    except CartItem.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Cart item not found'
        }, status=404)
    except Exception as e:
        print(f"âŒ Error updating cart quantity: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to update quantity: {str(e)}'
        }, status=500)

#todo###########wishlist services######################

# In views.py - Add these new endpoints

@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_service_to_wishlist(request):
    """
    Add a service to wishlist
    POST /api/services/wishlist/add/
    Body: {
        "service_id": "uuid",
        "service_option_id": "uuid"  # optional, uses first option if not provided
    }
    """
    user = request.user
    service_id = request.data.get('service_id')
    service_option_id = request.data.get('service_option_id')

    if not service_id:
        return Response({
            'success': False,
            'message': 'Service ID is required'
        }, status=400)

    try:
        service = Service.objects.get(id=service_id)

        # Get service option
        if service_option_id:
            try:
                service_option = ServiceOption.objects.get(id=service_option_id, service=service)
            except ServiceOption.DoesNotExist:
                return Response({
                    'success': False,
                    'message': 'Service option not found'
                }, status=404)
        else:
            # Use first available option
            service_option = service.options_set.first()
            if not service_option:
                return Response({
                    'success': False,
                    'message': 'No service options available'
                }, status=400)

        # Check if already in wishlist
        existing = ServiceWishlistItem.objects.filter(
            user=user,
            service_option=service_option
        ).exists()

        if existing:
            return Response({
                'success': False,
                'message': 'Service already in wishlist'
            }, status=200)

        # Add to wishlist
        wishlist_item = ServiceWishlistItem.objects.create(
            user=user,
            service=service,
            service_option=service_option
        )

        print(f"âœ… Service added to wishlist: {service.title}")

        return Response({
            'success': True,
            'message': 'Service added to wishlist successfully',
            'wishlist_count': ServiceWishlistItem.objects.filter(user=user).count(),
            'item': ServiceWishlistItemSerializer(wishlist_item, context={'request': request}).data
        }, status=201)

    except Service.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Service not found'
        }, status=404)
    except Exception as e:
        print(f"âŒ Error adding service to wishlist: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to add service to wishlist: {str(e)}'
        }, status=500)


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_service_from_wishlist(request, service_id):
    """
    Remove a service from wishlist
    DELETE /api/services/wishlist/remove/<service_id>/
    """
    user = request.user

    try:
        # Remove all wishlist items for this service
        deleted_count, _ = ServiceWishlistItem.objects.filter(
            user=user,
            service_id=service_id
        ).delete()

        if deleted_count == 0:
            return Response({
                'success': False,
                'message': 'Service not in wishlist'
            }, status=404)

        print(f"âœ… Service removed from wishlist: {service_id}")

        return Response({
            'success': True,
            'message': 'Service removed from wishlist',
            'wishlist_count': ServiceWishlistItem.objects.filter(user=user).count()
        })

    except Exception as e:
        print(f"âŒ Error removing service from wishlist: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to remove service: {str(e)}'
        }, status=500)





@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def check_service_in_wishlist(request):
    """
    Check if a service is in wishlist
    POST /api/services/wishlist/check/
    Body: {"service_id": "uuid"}
    """
    user = request.user
    service_id = request.data.get('service_id')

    if not service_id:
        return Response({
            'success': False,
            'message': 'Service ID is required'
        }, status=400)

    in_wishlist = ServiceWishlistItem.objects.filter(
        user=user,
        service_id=service_id
    ).exists()

    return Response({
        'success': True,
        'in_wishlist': in_wishlist,
        'service_id': service_id
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def change_password(request):
    """
    Change user password
    POST /api/change-password/
    Body: {
        "current_password": "oldpass123",
        "new_password": "newpass123",
        "confirm_password": "newpass123",
        "logout_all_devices": false  // Optional: logout from all other devices
    }
    """
    user = request.user

    current_password = request.data.get('current_password')
    new_password = request.data.get('new_password')
    confirm_password = request.data.get('confirm_password')
    logout_all = request.data.get('logout_all_devices', False)

    # Validate required fields
    if not all([current_password, new_password, confirm_password]):
        return Response({
            'success': False,
            'message': 'All password fields are required'
        }, status=400)

    # Verify current password
    if not check_password(current_password, user.password):
        return Response({
            'success': False,
            'message': 'Current password is incorrect'
        }, status=400)

    # Validate new password
    if len(new_password) < 6:
        return Response({
            'success': False,
            'message': 'New password must be at least 6 characters long'
        }, status=400)

    # Check if new password matches confirmation
    if new_password != confirm_password:
        return Response({
            'success': False,
            'message': 'New passwords do not match'
        }, status=400)

    # Check if new password is same as current
    if check_password(new_password, user.password):
        return Response({
            'success': False,
            'message': 'New password must be different from current password'
        }, status=400)

    try:
        # Update password
        user.password = make_password(new_password)
        user.save()

        # Optionally logout from all devices except current
        if logout_all:
            current_token = request.headers.get('Authorization', '').replace('token ', '')
            Token.objects.filter(user=user).exclude(token=current_token).delete()
            logout_message = ' You have been logged out from all other devices.'
        else:
            logout_message = ''

        print(f"✅ Password changed successfully for user: {user.email}")

        # Send notification (optional)
        try:
            Notification.objects.create(
                user=user,
                title='Password Changed',
                body=f'Your password was changed successfully on {timezone.now().strftime("%B %d, %Y at %I:%M %p")}',
                image=None
            )
        except Exception as e:
            print(f"Failed to send notification: {e}")

        return Response({
            'success': True,
            'message': f'Password changed successfully!{logout_message}'
        }, status=200)

    except Exception as e:
        print(f"❌ Error changing password: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to change password: {str(e)}'
        }, status=500)


# views.py - UPDATED: Real guest authentication

@api_view(['POST'])
def guest_login(request):
    """
    ✅ REAL Guest Login - Uses a shared guest account with real authentication
    POST /api/guest-login/
    Body: {"device_id": "optional_device_identifier"}
    """
    from uuid import uuid4
    from django.utils import timezone

    try:
        # Get or create the shared GUEST user
        guest_user, created = User.objects.get_or_create(
            email='guest@beautyhub.com',
            defaults={
                'phone': '0000000000',
                'fullname': 'Guest User',
                'password': make_password('guest_password_not_for_login'),  # Random password
            }
        )

        if created:
            print(f"✅ Created shared guest user: {guest_user.email}")

        # Generate a unique guest token (real token in database)
        device_id = request.data.get('device_id', str(uuid4()))
        guest_token = f"guest_{uuid4().hex}"

        # Create real Token entry (works with IsAuthenticatedUser)
        fcmtoken = request.data.get('fcmtoken', '')
        token_obj = Token.objects.create(
            token=guest_token,
            user=guest_user,
            fcmtoken=fcmtoken
        )

        print(f"🎭 Guest session created with real token: {guest_token[:20]}...")

        # Return same format as regular login
        return Response({
            'success': True,
            'is_guest': True,
            'token': f'token {guest_token}',  # Add 'token' prefix like regular login
            'guest_id': device_id[:12],
            'user': {
                'id': str(guest_user.id),
                'email': guest_user.email,
                'phone': guest_user.phone,
                'fullname': guest_user.fullname,
                'is_guest': True,
                'notifications': 0,
                'wishlist_count': guest_user.wishlist.count(),
                'cart_count': guest_user.cart.count(),
            },
            'message': '🎭 Browsing as guest. Sign up to save your data!',
        }, status=200)

    except Exception as e:
        print(f"❌ Guest login error: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'message': f'Failed to create guest session: {str(e)}'
        }, status=500)