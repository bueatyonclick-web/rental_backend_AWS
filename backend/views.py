# Fixed views.py - API endpoint improvements

import base64
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
from django.db.models import Q, Case, When, IntegerField, Value, F, Count, Avg, Sum
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

from backend.models import User, Otp, Token, Category, Slide, Product, PageItem, ProductOption, Order, \
    OrderedProduct, Service, ServiceBooking, ServicePageItem, ServiceCategory, ServiceOption, PasswordResetToken, \
    ProductImage, Vendor, VendorToken
from backend.serializers import UserSerializer, CategorySerializer, SlideSerializer, PageItemSerializer, \
    ProductSerializer, WishlistSerializer, CartSerializer, AddressSerializer, ItemOrderSerializer, \
    OrderDetailsSerializer, NotificationSerializer, OrderItemSerializer, ProductOptionSerializer, InformMeSerializer, \
    VersionCheckRequestSerializer, ServiceSerializer, ServiceCategorySerializer, ServicePageItemSerializer, \
    ServiceBookingSerializer, ServiceBookingDetailSerializer
from backend.utils import send_otp, token_response, send_password_reset_email, IsAuthenticatedUser, \
    new_token, IsAuthenticatedVendor
from core import settings
from core.settings import TEMPLATES_BASE_URL
from rest_framework import status as http_status

from . import models
from .authentication import VendorTokenAuthentication
from .serializers import PrivacyPolicySerializer

from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


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

    # Validate email format
    import re
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

    # Send OTP
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
    email = request.data.get('email')
    phone = request.data.get('phone')
    password = request.data.get('password')
    fullname = request.data.get('fullname')
    fcmtoken = request.data.get('fcmtoken')

    if email and phone and password and fullname:
        otp_obj = get_object_or_404(Otp, phone=phone, verified=True)
        otp_obj.delete()

        user = User()
        user.email = email
        user.phone = phone
        user.fullname = fullname
        user.password = make_password(password)
        user.save()
        return token_response(user, fcmtoken)

    else:
        return Response('data_missing', 400)


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
@permission_classes([IsAuthenticatedUser])
def logout(request):
    token_header = request.headers.get('Authorization')
    logout_all_param = request.GET.get('logout_all', 'false').lower()
    logout_all = logout_all_param == 'true'

    if logout_all:
        Token.objects.filter(user=request.user).delete()
    else:
        Token.objects.filter(token=token_header).delete()

    return Response({'message': 'logged_out'})


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def userdata(request):
    user = request.user
    data = UserSerializer(user, many=False).data
    return Response(data)


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


# Enhanced Home Screen API with slides integration
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def home_screen_data(request):
    """
    Get all data needed for home screen including user info, categories,
    promotional slides, page items, and recommended products
    """
    user = request.user

    # Get user data
    user_data = UserSerializer(user, many=False).data

    # Get categories
    categories = Category.objects.filter().order_by('position')[:6]
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
                print(f"Error processing slide {slide.id} in home API: {e}")
                continue
    except Exception as e:
        print(f"Error fetching slides in home API: {e}")
        slides_data = []

    # Enhanced page items with better product option handling
    page_items = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product',
        'product_options__images_set'
    ).order_by('category__position', 'position')[:10]

    page_items_data = []
    for page_item in page_items:
        product_options_data = []
        for option in page_item.product_options.all()[:8]:
            first_image = option.images_set.first()
            image_url = None
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

            product_data = {
                'id': str(option.product.id),
                'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                'price': option.product.price,
                'offer_price': option.product.offer_price,
                'image': image_url,
                'option_id': str(option.id),
                'option_name': option.option,
                'quantity_available': option.quantity,
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

    # Enhanced recommended products with image handling
    recommended_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        options_set__quantity__gt=0
    ).order_by('-created_at')[:6]

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

    # Get cart count for user
    cart_count = user.cart.count()

    # Get wishlist count
    wishlist_count = user.wishlist.count()

    return Response({
        'user': user_data,
        'categories': categories_data,
        'promotional_slides': slides_data,
        'page_items': page_items_data,
        'recommended_products': products_data,
        'cart_count': cart_count,
        'wishlist_count': wishlist_count,
        'unread_notifications': user_data['notifications']
    })


# Search and filtering APIs
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def search_products(request):
    """
    Enhanced product search with advanced filtering, sorting, and pagination
    """
    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', None)
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    sort_by = request.GET.get('sort', 'relevance')
    page = request.GET.get('page', 1)

    products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(options_set__quantity__gt=0).distinct()

    if query:
        products = products.filter(
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query) |
            Q(options_set__option__icontains=query)
        ).distinct()

    if category_id:
        try:
            products = products.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

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
    else:
        products = products.order_by('-created_at')

    # Pagination
    paginator = Paginator(products, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

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


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_cart(request, product_option_id):
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if not user.cart.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in cart'}, status=400)

    user.cart.remove(product_option)

    return Response({
        'message': 'Removed from cart successfully',
        'cart_count': user.cart.count()
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_wishlist(request):
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if user.wishlist.filter(id=product_option_id).exists():
        return Response({'message': 'Product already in wishlist'}, status=200)

    user.wishlist.add(product_option)

    return Response({
        'message': 'Added to wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_wishlist(request, product_option_id):
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

# Fixed category products API
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def category_products(request, category_id):
    """
    Enhanced category products with filtering and sorting
    """
    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return Response({'error': 'Category not found'}, status=404)

    # Get query parameters
    sort_by = request.GET.get('sort', 'relevance')
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    page = request.GET.get('page', 1)

    # Get products in this category with stock
    products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        category=category,
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

    # Apply sorting (same logic as search_products)
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
        products = products.order_by('-created_at')

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

    # Serialize category data
    category_data = CategorySerializer(category, context={'request': request}).data

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
@permission_classes([IsAuthenticatedUser])
def all_categories(request):
    """
    Get all categories with product counts for categories view
    """
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
            'title': category.name,  # Use title for consistency with products
            'image': request.build_absolute_uri(category.image.url) if category.image and request else (
                category.image.url if category.image else None),
            'price': 0,  # Categories don't have prices
            'offer_price': 0,
            'category_data': {  # Nested category data for navigation
                'id': category.id,
                'name': category.name,
                'image': request.build_absolute_uri(category.image.url) if category.image and request else (
                    category.image.url if category.image else None),
                'product_count': category.in_stock_count,
            }
        }
        categories_data.append(category_data)

    return Response({
        'products': categories_data,  # Use 'products' key for consistency with ViewAllScreen
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
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
            products = products.order_by('-created_at')

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
def remove_from_cart(request, product_option_id):
    """
    Remove a product option from user's cart
    """
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if not user.cart.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in cart'}, status=400)

    user.cart.remove(product_option)

    return Response({
        'message': 'Removed from cart successfully',
        'cart_count': user.cart.count()
    })


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
            # Get first image
            first_image = option.images_set.first()
            image_url = None
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

            product_data = {
                'id': str(option.product.id),
                'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                'price': option.product.price,
                'offer_price': option.product.offer_price,
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
                # Get first image
                first_image = option.images_set.first()
                image_url = None
                if first_image and request:
                    image_url = request.build_absolute_uri(first_image.image.url)
                elif first_image:
                    image_url = first_image.image.url

                product_data = {
                    'id': str(option.product.id),
                    'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                    'price': option.product.price,
                    'offer_price': option.product.offer_price,
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
    Apply a coupon code to get discount
    """
    user = request.user
    coupon_code = request.data.get('coupon_code', '').strip().upper()

    if not coupon_code:
        return Response({'error': 'Coupon code is required'}, status=400)

    # Get user's cart items to calculate discount on
    cart_items = user.cart.select_related('product').all()

    if not cart_items:
        return Response({'error': 'Cart is empty'}, status=400)

    # Calculate cart total
    cart_total = sum(
        item.product.offer_price if item.product.offer_price > 0 else item.product.price
        for item in cart_items
    )

    # Define available coupons (you can move this to a database model later)
    available_coupons = {
        'WELCOME10': {'discount_percent': 10, 'min_amount': 500, 'max_discount': 200},
        'SAVE20': {'discount_percent': 20, 'min_amount': 1000, 'max_discount': 500},
        'FLAT50': {'discount_amount': 50, 'min_amount': 300, 'max_discount': 50},
        'NEWUSER': {'discount_percent': 15, 'min_amount': 800, 'max_discount': 300},
        'FESTIVAL25': {'discount_percent': 25, 'min_amount': 1500, 'max_discount': 750},
    }

    if coupon_code not in available_coupons:
        return Response({
            'success': False,
            'message': 'Invalid coupon code',
            'discount_amount': 0
        }, status=400)

    coupon = available_coupons[coupon_code]

    # Check minimum amount requirement
    if cart_total < coupon['min_amount']:
        return Response({
            'success': False,
            'message': f'Minimum order value of ₹{coupon["min_amount"]} required for this coupon',
            'discount_amount': 0
        }, status=400)

    # Calculate discount
    if 'discount_percent' in coupon:
        discount_amount = (cart_total * coupon['discount_percent']) / 100
    else:
        discount_amount = coupon.get('discount_amount', 0)

    # Apply maximum discount limit
    discount_amount = min(discount_amount, coupon['max_discount'])
    discount_amount = int(discount_amount)  # Convert to integer

    return Response({
        'success': True,
        'message': f'Coupon applied successfully! You saved ₹{discount_amount}',
        'discount_amount': discount_amount,
        'coupon_code': coupon_code
    })


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
    Clear all items from user's cart
    """
    user = request.user
    user.cart.clear()

    return Response({
        'message': 'Cart cleared successfully',
        'cart_count': 0
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def move_to_wishlist(request):
    """
    Move item from cart to wishlist
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    # Check if item is in cart
    if not user.cart.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in cart'}, status=400)

    # Move from cart to wishlist
    user.cart.remove(product_option)

    if not user.wishlist.filter(id=product_option_id).exists():
        user.wishlist.add(product_option)

    return Response({
        'message': 'Item moved to wishlist successfully',
        'cart_count': user.cart.count(),
        'wishlist_count': user.wishlist.count()
    })


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


# Enhanced get_cart_items with better calculations
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_cart_items_enhanced(request):
    """
    Enhanced version of get_cart_items with better data structure
    """
    user = request.user
    cart_items = user.cart.select_related('product').prefetch_related('images_set').all()

    cart_data = []
    total_amount = 0
    offer_amount = 0
    total_savings = 0
    delivery_charges = 0

    for item in cart_items:
        # Get first image
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        # Calculate prices
        original_price = item.product.price
        effective_price = item.product.offer_price if item.product.offer_price > 0 else item.product.price
        savings_per_item = original_price - effective_price

        # Add to totals (assuming quantity = 1 since your current model doesn't support quantities)
        total_amount += original_price
        offer_amount += effective_price
        total_savings += savings_per_item
        delivery_charges += item.product.delivery_charge

        item_data = {
            'id': str(item.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': original_price,
            'offer_price': effective_price,
            'quantity': 1,  # Since your model doesn't support quantities yet
            'cod': item.product.cod,
            'delivery_charge': item.product.delivery_charge,
            'savings': savings_per_item,
            'product_id': str(item.product.id),
            'in_stock': item.quantity > 0,
            'stock_quantity': item.quantity,
        }
        cart_data.append(item_data)

    # Calculate final amounts
    final_amount = offer_amount + delivery_charges

    # Check for free delivery threshold
    free_delivery_threshold = 500  # You can make this configurable
    if offer_amount >= free_delivery_threshold:
        delivery_charges = 0
        final_amount = offer_amount

    return Response({
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
            'similar_products': [],  # You can implement product recommendations
            'frequently_bought_together': []  # You can implement this feature
        }
    })





# Add these to your views.py file

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_order_tracking(request, order_id):
    """
    Get detailed tracking information for a specific order
    """
    user = request.user

    try:
        order = Order.objects.select_related('user').get(id=order_id, user=user)
    except Order.DoesNotExist:
        return Response({'error': 'Order not found'}, status=404)

    # Get ordered products for this order
    ordered_products = OrderedProduct.objects.select_related(
        'product_option__product'
    ).prefetch_related(
        'product_option__images_set'
    ).filter(order=order)

    # Create tracking timeline based on order status
    tracking_steps = _generate_tracking_timeline(order, ordered_products)

    # ❌ REMOVED: Get delivery partner info
    # delivery_partner = _get_delivery_partner_info(order)

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

    return Response({
        'order': {
            'id': str(order.id),
            'order_number': f"BH{str(order.id)[:8].upper()}",
            'status': order.tx_status,
            'payment_mode': order.payment_mode,
            'total_amount': order.tx_amount,
            'created_at': order.created_at.isoformat(),
            'delivery_address': order.address,
            'expected_delivery': _calculate_expected_delivery(order),
        },
        'products': products_data,
        'tracking_timeline': tracking_steps,
        # ❌ REMOVED: delivery_partner field
        # 'delivery_partner': delivery_partner,
        'support_info': {
            'phone': '+91 98765 43210',
            'email': 'support@beautyhub.com',
            'chat_available': True,
        }
    })

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_user_orders(request):
    """
    Get all orders for the authenticated user with basic tracking info
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

    # Pagination
    paginator = Paginator(orders, 10)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

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

        order_data = {
            'id': str(order.id),
            'order_number': f"BH{str(order.id)[:8].upper()}",
            'status': order.tx_status,
            'current_status': current_status,
            'total_amount': order.tx_amount,
            'total_items': total_items,
            'created_at': order.created_at.isoformat(),
            'expected_delivery': _calculate_expected_delivery(order),
            'product_preview': {
                'title': product_title,
                'image': product_image,
                'additional_items': max(0, total_items - 1)
            }
        }
        orders_data.append(order_data)

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
    Update rating for a delivered product
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

    # Update rating
    old_rating = ordered_product.rating
    ordered_product.rating = rating
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
    """Generate tracking timeline based on order and product status"""
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
    expected_delivery = _calculate_expected_delivery(order)

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
    """Calculate expected delivery time"""
    from django.utils import timezone

    # Add 2 days for standard delivery
    expected = order.created_at + timezone.timedelta(days=2)
    return expected.strftime("%d %b %Y, %I:%M %p")


def _get_current_tracking_status(order):
    """Get current human-readable tracking status"""
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
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def home_screen_data_enhanced(request):
    """
    Enhanced home screen data with improved slides handling
    """
    user = request.user

    # Get user data
    user_data = UserSerializer(user, many=False).data

    # Get categories
    categories = Category.objects.filter().order_by('position')[:6]
    categories_data = CategorySerializer(categories, many=True, context={'request': request}).data

    # Get promotional slides with better error handling
    try:
        slides = Slide.objects.all().order_by('position')
        slides_data = []
        for slide in slides:
            try:
                slide_data = {
                    'position': slide.position,
                    'image': request.build_absolute_uri(slide.image.url) if slide.image and request else (
                        slide.image.url if slide.image else None),
                    'id': slide.id,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id}: {e}")
                continue
    except Exception as e:
        print(f"Error fetching slides: {e}")
        slides_data = []

    # Enhanced page items with better product option handling
    page_items = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product',
        'product_options__images_set'
    ).order_by('category__position', 'position')[:10]

    page_items_data = []
    for page_item in page_items:
        product_options_data = []
        for option in page_item.product_options.all()[:8]:
            # Get the first image for this option
            first_image = option.images_set.first()
            image_url = None
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

            product_data = {
                'id': str(option.product.id),  # Ensure string format
                'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                'price': option.product.price,
                'offer_price': option.product.offer_price,
                'image': image_url,
                # Add product option details for cart/wishlist operations
                'option_id': str(option.id),
                'option_name': option.option,
                'quantity_available': option.quantity,
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

    # Enhanced recommended products with image handling
    recommended_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        options_set__quantity__gt=0
    ).order_by('-created_at')[:6]

    # Enhanced product serialization with direct image access
    products_data = []
    for product in recommended_products:
        # Get first option and its first image
        first_option = product.options_set.first()
        first_image = None
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        # Create enhanced product data structure
        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            # Direct image access for easier frontend handling
            'image': image_url,
            'options': []
        }

        # Add options data
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

    # Get cart count for user
    cart_count = user.cart.count()

    # Get wishlist count
    wishlist_count = user.wishlist.count()

    return Response({
        'user': user_data,
        'categories': categories_data,
        'promotional_slides': slides_data,  # Enhanced slides data
        'page_items': page_items_data,
        'recommended_products': products_data,
        'cart_count': cart_count,
        'wishlist_count': wishlist_count,
        'unread_notifications': user_data['notifications']
    })

# Alternative: Update your existing home_screen_data function
# Replace your existing home_screen_data function with home_screen_data_enhanced
# or modify your existing function to include the enhanced slides handling

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

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_addresses(request):
    """
    Get all addresses for the authenticated user
    URL: /api/addresses/
    """
    user = request.user

    try:
        # Since the current model stores only one address per user,
        # we'll return it as a single address in a list format
        addresses = []

        if user.name or user.address or user.contact_no:
            address_data = {
                'id': 1,  # Static ID since there's only one address per user
                'type': 'Home',  # Default type
                'name': user.name or user.fullname,
                'address': user.address or '',
                'contact_no': user.contact_no or user.phone,
                'pincode': user.pincode,
                'district': user.district or '',
                'state': user.state or '',
                'is_default': True,  # Always default since there's only one
                'created_at': user.created_at.isoformat() if user.created_at else None
            }
            addresses.append(address_data)

        return Response({
            'success': True,
            'addresses': addresses,
            'message': 'Addresses fetched successfully'
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to fetch addresses: {str(e)}',
            'addresses': []
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_address(request):
    """
    Add a new address for the user
    URL: /api/addresses/add/
    """
    user = request.user

    try:
        data = request.data

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

        # Update user address fields
        user.name = data.get('name')
        user.address = data.get('address')
        user.contact_no = data.get('contact_no')
        user.pincode = int(pincode) if pincode else None
        user.district = data.get('district', '')
        user.state = data.get('state', '')
        user.save()

        # Return the created address
        address_data = {
            'id': 1,
            'type': data.get('type', 'Home'),
            'name': user.name,
            'address': user.address,
            'contact_no': user.contact_no,
            'pincode': user.pincode,
            'district': user.district,
            'state': user.state,
            'is_default': True,
            'created_at': user.created_at.isoformat() if user.created_at else None
        }

        return Response({
            'success': True,
            'message': 'Address added successfully',
            'address': address_data
        }, status=201)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to add address: {str(e)}'
        }, status=500)


@api_view(['PUT'])
@permission_classes([IsAuthenticatedUser])
def update_address(request, address_id):
    """
    Update an existing address
    URL: /api/addresses/<address_id>/update/
    """
    user = request.user

    try:
        data = request.data

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

        # Update user address fields
        user.name = data.get('name')
        user.address = data.get('address')
        user.contact_no = data.get('contact_no')
        user.pincode = int(pincode) if pincode else None
        user.district = data.get('district', '')
        user.state = data.get('state', '')
        user.save()

        # Return the updated address
        address_data = {
            'id': int(address_id),
            'type': data.get('type', 'Home'),
            'name': user.name,
            'address': user.address,
            'contact_no': user.contact_no,
            'pincode': user.pincode,
            'district': user.district,
            'state': user.state,
            'is_default': True,
            'created_at': user.created_at.isoformat() if user.created_at else None
        }

        return Response({
            'success': True,
            'message': 'Address updated successfully',
            'address': address_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to update address: {str(e)}'
        }, status=500)


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def delete_address(request, address_id):
    """
    Delete an address
    URL: /api/addresses/<address_id>/delete/
    """
    user = request.user

    try:
        # Clear address fields
        user.name = ''
        user.address = ''
        user.contact_no = ''
        user.pincode = None
        user.district = ''
        user.state = ''
        user.save()

        return Response({
            'success': True,
            'message': 'Address deleted successfully'
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to delete address: {str(e)}'
        }, status=500)


# Add these address management views to your views.py file

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_addresses(request):
    """
    Get all addresses for the authenticated user
    URL: /api/addresses/
    """
    user = request.user

    try:
        # Since the current model stores only one address per user,
        # we'll return it as a single address in a list format
        addresses = []

        if user.name or user.address or user.contact_no:
            address_data = {
                'id': 1,  # Static ID since there's only one address per user
                'type': 'Home',  # Default type
                'name': user.name or user.fullname,
                'address': user.address or '',
                'contact_no': user.contact_no or user.phone,
                'pincode': user.pincode,
                'district': user.district or '',
                'state': user.state or '',
                'is_default': True,  # Always default since there's only one
                'created_at': user.created_at.isoformat() if user.created_at else None
            }
            addresses.append(address_data)

        return Response({
            'success': True,
            'addresses': addresses,
            'message': 'Addresses fetched successfully'
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to fetch addresses: {str(e)}',
            'addresses': []
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_address(request):
    """
    Add a new address for the user
    URL: /api/addresses/add/
    """
    user = request.user

    try:
        data = request.data

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

        # Update user address fields
        user.name = data.get('name')
        user.address = data.get('address')
        user.contact_no = data.get('contact_no')
        user.pincode = int(pincode) if pincode else None
        user.district = data.get('district', '')
        user.state = data.get('state', '')
        user.save()

        # Return the created address
        address_data = {
            'id': 1,
            'type': data.get('type', 'Home'),
            'name': user.name,
            'address': user.address,
            'contact_no': user.contact_no,
            'pincode': user.pincode,
            'district': user.district,
            'state': user.state,
            'is_default': True,
            'created_at': user.created_at.isoformat() if user.created_at else None
        }

        return Response({
            'success': True,
            'message': 'Address added successfully',
            'address': address_data
        }, status=201)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to add address: {str(e)}'
        }, status=500)


@api_view(['PUT'])
@permission_classes([IsAuthenticatedUser])
def update_address(request, address_id):
    """
    Update an existing address
    URL: /api/addresses/<address_id>/update/
    """
    user = request.user

    try:
        data = request.data

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

        # Update user address fields
        user.name = data.get('name')
        user.address = data.get('address')
        user.contact_no = data.get('contact_no')
        user.pincode = int(pincode) if pincode else None
        user.district = data.get('district', '')
        user.state = data.get('state', '')
        user.save()

        # Return the updated address
        address_data = {
            'id': int(address_id),
            'type': data.get('type', 'Home'),
            'name': user.name,
            'address': user.address,
            'contact_no': user.contact_no,
            'pincode': user.pincode,
            'district': user.district,
            'state': user.state,
            'is_default': True,
            'created_at': user.created_at.isoformat() if user.created_at else None
        }

        return Response({
            'success': True,
            'message': 'Address updated successfully',
            'address': address_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to update address: {str(e)}'
        }, status=500)


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def delete_address(request, address_id):
    """
    Delete an address
    URL: /api/addresses/<address_id>/delete/
    """
    user = request.user

    try:
        # Clear address fields
        user.name = ''
        user.address = ''
        user.contact_no = ''
        user.pincode = None
        user.district = ''
        user.state = ''
        user.save()

        return Response({
            'success': True,
            'message': 'Address deleted successfully'
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to delete address: {str(e)}'
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

            # Create Order
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

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_wishlist_items_enhanced(request):
    """
    Get enhanced wishlist items with complete product details
    """
    user = request.user
    wishlist_items = user.wishlist.select_related('product__category').prefetch_related('images_set').all()

    wishlist_data = []
    for item in wishlist_items:
        # Get first image
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        # Calculate ratings
        total_ratings = (item.product.star_5 + item.product.star_4 +
                         item.product.star_3 + item.product.star_2 + item.product.star_1)

        if total_ratings > 0:
            average_rating = (
                                     (item.product.star_5 * 5) + (item.product.star_4 * 4) +
                                     (item.product.star_3 * 3) + (item.product.star_2 * 2) +
                                     (item.product.star_1 * 1)
                             ) / total_ratings
            average_rating = round(average_rating, 1)
        else:
            average_rating = 0

        # Calculate discount percentage
        discount_percentage = 0
        if item.product.offer_price > 0 and item.product.offer_price < item.product.price:
            discount_percentage = round(((item.product.price - item.product.offer_price) / item.product.price) * 100)

        item_data = {
            'id': str(item.id),
            'product_id': str(item.product.id),
            'name': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.offer_price if item.product.offer_price > 0 else item.product.price,
            'original_price': item.product.price,
            'offer_price': item.product.offer_price,
            'discount_percentage': discount_percentage,
            'rating': average_rating,
            'reviews': total_ratings,
            'in_stock': item.quantity > 0,
            'category': item.product.category.name if item.product.category else 'Other',
            'cod_available': item.product.cod,
            'delivery_charge': item.product.delivery_charge,
            'quantity_available': item.quantity,
            'in_cart': user.cart.filter(id=item.id).exists(),
            'created_at': item.product.created_at.isoformat(),
        }
        wishlist_data.append(item_data)

    return Response({
        'success': True,
        'wishlist_items': wishlist_data,
        'total_items': len(wishlist_data),
        'message': 'Wishlist items fetched successfully'
    })


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


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_all_services(request):
    """
    Get all available services with filtering and pagination
    """
    category_id = request.GET.get('category', None)
    sort_by = request.GET.get('sort', 'relevance')
    page = request.GET.get('page', 1)

    # Get services
    services = Service.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(availability=True)

    # Apply category filter
    if category_id:
        try:
            services = services.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    # Apply sorting
    if sort_by == 'price_low_high':
        services = services.order_by('base_price')
    elif sort_by == 'price_high_low':
        services = services.order_by('-base_price')
    elif sort_by == 'rating':
        services = services.order_by('-rating', '-total_reviews')
    elif sort_by == 'experience':
        services = services.order_by('-experience_years')
    else:
        services = services.order_by('-created_at')

    # Pagination
    paginator = Paginator(services, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Serialize services
    services_data = ServiceSerializer(
        page_obj,
        many=True,
        context={'request': request}
    ).data

    return Response({
        'services': services_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_services': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_service_categories(request):
    """
    Get all service categories
    """
    categories = ServiceCategory.objects.all().order_by('position')
    categories_data = ServiceCategorySerializer(
        categories,
        many=True,
        context={'request': request}
    ).data

    return Response({
        'categories': categories_data,
        'total_categories': len(categories_data)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_service_details(request, service_id):
    """
    Get detailed information about a specific service
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


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_service_page_items(request):
    """
    Get service page items for home screen
    """
    category_id = request.GET.get('category', None)

    page_items = ServicePageItem.objects.select_related('category').prefetch_related(
        'service_options__service',
        'service_options__images_set'
    ).order_by('category__position', 'position')

    if category_id:
        try:
            page_items = page_items.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    page_items_data = ServicePageItemSerializer(
        page_items[:10],
        many=True,
        context={'request': request}
    ).data

    return Response({
        'page_items': page_items_data
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
            total_amount=service_option.price,
            notes=notes,
            status='PENDING',
            payment_status='PENDING'
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
@permission_classes([IsAuthenticatedUser])
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

    # Get all bookings for this service in the specified month
    service_options = service.options_set.all()
    bookings = ServiceBooking.objects.filter(
        service_option__in=service_options,
        booking_date__year=year,
        booking_date__month=month,
        status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
    )

    # Extract booked dates (days of the month)
    booked_dates = list(bookings.values_list('booking_date__day', flat=True).distinct())

    # Get total days in month
    total_days = monthrange(year, month)[1]

    # Get current date
    today = timezone.now().date()

    # Calculate available dates (exclude past dates and booked dates)
    available_dates = []
    for day in range(1, total_days + 1):
        from datetime import datetime as dt
        check_date = dt(year, month, day).date()
        if check_date >= today and day not in booked_dates:
            available_dates.append(day)

    return Response({
        'success': True,
        'year': year,
        'month': month,
        'booked_dates': booked_dates,
        'available_dates': available_dates,
        'total_days': total_days
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
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
    booking.notes = f"{booking.notes}\nCancellation Reason: {cancellation_reason}"
    booking.save()

    # Send notification


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
    Rate a completed service booking

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
        booking = ServiceBooking.objects.get(
            id=booking_id,
            user=user
        )
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

    # Update booking notes with rating
    rating_note = f"\n\nRating: {rating_int}/5"
    if review_text:
        rating_note += f"\nReview: {review_text}"

    booking.notes = f"{booking.notes}{rating_note}"
    booking.save()

    # Update service rating (optional - aggregate ratings)
    service = booking.service_option.service
    # You could implement a more sophisticated rating system here
    # For now, we'll just store it in the booking notes

    return Response({
        'success': True,
        'message': 'Rating submitted successfully',
        'rating': {
            'booking_id': str(booking.id),
            'overall_rating': rating_int,
            'review_text': review_text
        }
    })


@api_view(['POST'])
def forgot_password(request):
    """
    Request OTP for password reset
    Body: { "phone": "1234567890" }
    """
    phone = request.data.get('phone')

    if not phone:
        return Response({
            'success': False,
            'message': 'Phone number is required'
        }, status=400)

    # Check if user exists with this phone number
    try:
        user = User.objects.get(phone=phone)
    except User.DoesNotExist:
        return Response({
            'success': False,
            'message': 'No account found with this phone number'
        }, status=404)

    # Generate and send OTP
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

    # In production, send actual SMS here
    # For now, just print it
    print(f"Password Reset OTP for {phone}: {otp}")

    return Response({
        'success': True,
        'message': 'OTP sent successfully to your phone number',
        'phone': phone
    }, status=200)


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


@api_view(['POST'])
def vendor_login(request):
    """
    Vendor login - Only admin-created vendors can login
    Body: {
        "email": "vendor@example.com",
        "password": "admin_created_password"
    }
    """
    email = request.data.get('email')
    password = request.data.get('password')
    fcmtoken = request.data.get('fcmtoken', '')

    if not email or not password:
        return Response({
            'success': False,
            'message': 'Email and password are required'
        }, status=400)

    try:
        # Get vendor by email
        vendor = Vendor.objects.get(email=email)

        # Check if vendor is active
        if not vendor.is_active:
            return Response({
                'success': False,
                'message': 'Your vendor account is deactivated. Please contact admin.'
            }, status=403)

        # Check password
        if check_password(password, vendor.password):
            # Generate token
            token = new_token()
            VendorToken.objects.create(token=token, vendor=vendor, fcmtoken=fcmtoken)

            return Response({
                'success': True,
                'message': 'Login successful',
                'token': f'token {token}',
                'vendor': {
                    'id': vendor.id,
                    'vendor_id': vendor.vendor_id,
                    'name': vendor.name,
                    'email': vendor.email,
                    'phone': vendor.phone,
                }
            })
        else:
            return Response({
                'success': False,
                'message': 'Incorrect password'
            }, status=400)

    except Vendor.DoesNotExist:
        return Response({
            'success': False,
            'message': 'No vendor account found with this email. Please contact admin to create your account.'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Login failed: {str(e)}'
        }, status=500)



@api_view(['GET'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_dashboard(request):
    """
    Get vendor dashboard statistics - Only shows data for logged-in vendor
    """
    vendor = request.user  # This is now a Vendor instance

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

        # Get recent orders (last 30 days)
        from django.utils import timezone
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        recent_orders = OrderedProduct.objects.filter(
            product_option__product__in=products,
            created_at__gte=thirty_days_ago
        ).count()

        # Calculate total revenue
        total_revenue = OrderedProduct.objects.filter(
            product_option__product__in=products,
            status='DELIVERED'
        ).aggregate(total=Sum('tx_price'))['total'] or 0

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
                'total_revenue': total_revenue
            }
        })
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to load dashboard: {str(e)}'
        }, status=500)


# Product Management
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

    # Order by creation date
    products = products.order_by('-created_at')

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


@api_view(['POST'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_create_product(request):
    """
    Create a new product - Automatically assigns to logged-in vendor
    """
    vendor = request.user

    title = request.data.get('title')
    description = request.data.get('description', '')
    price = request.data.get('price')
    offer_price = request.data.get('offer_price', 0)
    delivery_charge = request.data.get('delivery_charge', 0)
    cod = request.data.get('cod', True)
    category_id = request.data.get('category_id')

    # Validate required fields
    if not title or not price:
        return Response({
            'success': False,
            'message': 'Title and price are required'
        }, status=400)

    # Validate category
    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Invalid category'
        }, status=400)

    # Validate prices
    try:
        price = int(price)
        offer_price = int(offer_price)
        delivery_charge = int(delivery_charge)

        if price <= 0:
            return Response({
                'success': False,
                'message': 'Price must be greater than 0'
            }, status=400)

        if offer_price > price:
            return Response({
                'success': False,
                'message': 'Offer price cannot be greater than original price'
            }, status=400)
    except (ValueError, TypeError):
        return Response({
            'success': False,
            'message': 'Invalid price format'
        }, status=400)

    try:
        # Create product and assign to vendor
        product = Product.objects.create(
            vendor=vendor,  # Assign to logged-in vendor
            category=category,
            title=title,
            description=description,
            price=price,
            offer_price=offer_price,
            delivery_charge=delivery_charge,
            cod=cod
        )

        return Response({
            'success': True,
            'message': 'Product created successfully',
            'product': {
                'id': str(product.id),
                'title': product.title,
                'price': product.price,
                'offer_price': product.offer_price,
                'vendor': vendor.vendor_id
            }
        }, status=201)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to create product: {str(e)}'
        }, status=500)


@api_view(['PUT'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_update_product(request, product_id):
    """
    Update product - Only vendor's own products
    """
    vendor = request.user

    try:
        # Ensure product belongs to this vendor
        product = Product.objects.get(id=product_id, vendor=vendor)
    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found or you do not have permission to edit it'
        }, status=404)

    # Update fields if provided
    if 'title' in request.data:
        product.title = request.data['title']

    if 'description' in request.data:
        product.description = request.data['description']

    if 'price' in request.data:
        try:
            price = int(request.data['price'])
            if price <= 0:
                return Response({
                    'success': False,
                    'message': 'Price must be greater than 0'
                }, status=400)
            product.price = price
        except (ValueError, TypeError):
            return Response({
                'success': False,
                'message': 'Invalid price format'
            }, status=400)

    if 'offer_price' in request.data:
        try:
            offer_price = int(request.data['offer_price'])
            if offer_price > product.price:
                return Response({
                    'success': False,
                    'message': 'Offer price cannot be greater than original price'
                }, status=400)
            product.offer_price = offer_price
        except (ValueError, TypeError):
            return Response({
                'success': False,
                'message': 'Invalid offer price format'
            }, status=400)

    if 'delivery_charge' in request.data:
        try:
            product.delivery_charge = int(request.data['delivery_charge'])
        except (ValueError, TypeError):
            return Response({
                'success': False,
                'message': 'Invalid delivery charge format'
            }, status=400)

    if 'cod' in request.data:
        product.cod = request.data['cod']

    if 'category_id' in request.data:
        try:
            category = Category.objects.get(id=request.data['category_id'])
            product.category = category
        except Category.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Invalid category'
            }, status=400)

    try:
        product.save()

        return Response({
            'success': True,
            'message': 'Product updated successfully',
            'product': ProductSerializer(product, context={'request': request}).data
        })

    except Exception as e:
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
@api_view(['POST'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_create_product_option(request):
    """
    Create a new product option
    Body: {
        "product_id": "uuid",
        "option": "Size M / Color Red",
        "quantity": 100
    }
    """
    product_id = request.data.get('product_id')
    option = request.data.get('option', '')
    quantity = request.data.get('quantity', 0)

    if not product_id:
        return Response({
            'success': False,
            'message': 'Product ID is required'
        }, status=400)

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

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
        # Create product option
        product_option = ProductOption.objects.create(
            product=product,
            option=option,
            quantity=quantity
        )

        return Response({
            'success': True,
            'message': 'Product option created successfully',
            'product_option': {
                'id': str(product_option.id),
                'option': product_option.option,
                'quantity': product_option.quantity,
                'product': str(product_option.product.id)
            }
        }, status=201)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to create product option: {str(e)}'
        }, status=500)


@api_view(['PUT'])
@permission_classes([IsAuthenticatedVendor])
@authentication_classes([VendorTokenAuthentication])
def vendor_update_product_option(request, option_id):
    """
    Update a product option
    Body: {
        "option": "Updated Size L / Color Blue",
        "quantity": 150
    }
    """
    try:
        product_option = ProductOption.objects.get(id=option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product option not found'
        }, status=404)

    if 'option' in request.data:
        product_option.option = request.data['option']

    if 'quantity' in request.data:
        try:
            quantity = int(request.data['quantity'])
            if quantity < 0:
                return Response({
                    'success': False,
                    'message': 'Quantity cannot be negative'
                }, status=400)
            product_option.quantity = quantity
        except (ValueError, TypeError):
            return Response({
                'success': False,
                'message': 'Invalid quantity format'
            }, status=400)

    try:
        product_option.save()

        return Response({
            'success': True,
            'message': 'Product option updated successfully',
            'product_option': ProductOptionSerializer(
                product_option,
                context={'request': request}
            ).data
        })

    except Exception as e:
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
    categories = Category.objects.all().order_by('position')
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